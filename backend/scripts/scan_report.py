"""What happened in a scanning session.

Reads `scan_events` and answers the questions that matter after a real batch: how
accurate was the first match, how long did frames take, where did that time go, and
how many frames were spent on views that held no card at all.

Usage, against the running stack::

    docker compose cp backend/scripts/scan_report.py app:/tmp/scan_report.py
    docker compose exec app sh -c 'cd /srv && PYTHONPATH=/srv python /tmp/scan_report.py'
    docker compose exec app sh -c 'cd /srv && PYTHONPATH=/srv python /tmp/scan_report.py --all'

Read-only.
"""

from __future__ import annotations

import argparse
import statistics
import sys
from collections import Counter, defaultdict

from sqlalchemy import desc, select

from app.db import session_scope
from app.models import Card, ScanEvent, ScanSession


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(len(ordered) * fraction))]


def main(argv: list[str] | None = None) -> int:
    """Summarise the most recent scanning session, or every session with --all."""
    parser = argparse.ArgumentParser(description="Summarise a scanning session")
    parser.add_argument("--all", action="store_true", help="every session, not just the last")
    args = parser.parse_args(argv)

    with session_scope() as db:
        statement = select(ScanEvent).order_by(ScanEvent.id)
        if not args.all:
            session = db.scalars(
                select(ScanSession).order_by(desc(ScanSession.started_at)).limit(1)
            ).first()
            if session is None:
                print("no scan sessions yet")
                return 1
            statement = statement.where(ScanEvent.session_id == session.id)
            print(f"session {session.id[:8]}   started {session.started_at}")
        else:
            print("all sessions")

        events = list(db.scalars(statement))
        if not events:
            print("no frames recorded")
            return 1
        print(f"{len(events)} frames analysed\n")

        # --- what each frame concluded ----------------------------------------
        print("frames by outcome:")
        methods = Counter(event.method for event in events)
        for method, count in methods.most_common():
            latencies = [e.latency_ms for e in events if e.method == method and e.latency_ms]
            median = statistics.median(latencies) if latencies else 0.0
            share = 100 * count / len(events)
            print(f"  {method:<10} {count:>5}  {share:5.1f}%   median {median:6.0f} ms")

        # --- where the time went ----------------------------------------------
        stages: defaultdict[str, list[float]] = defaultdict(list)
        detailed = 0
        no_card = 0
        clipped = 0
        for event in events:
            # Events recorded before migration 0005 carry no detail. Counting those as
            # "no card found" would report a session as almost entirely empty frames.
            if event.detail_json is None:
                continue
            detailed += 1
            detail = event.detail_json
            for stage, value in (detail.get("stage_ms") or {}).items():
                stages[stage].append(float(value))
            if not detail.get("detections"):
                no_card += 1
            clipped += int(detail.get("clipped") or 0)

        if detailed and detailed < len(events):
            print(
                f"\n({detailed} of {len(events)} frames carry per-stage "
                "detail; the rest predate it)"
            )

        if stages:
            print("\ntime per stage (only counting frames that reached it):")
            order = ["decode", "detect", "focus", "visual", "collector", "name", "event", "capture"]
            for stage in sorted(stages, key=lambda s: order.index(s) if s in order else 99):
                values = stages[stage]
                print(
                    f"  {stage:<10} ran on {len(values):>5} frames   "
                    f"median {statistics.median(values):6.1f} ms   "
                    f"total {sum(values) / 1000:6.1f} s"
                )

        if detailed:
            print(
                f"\nframes with no card found: {no_card}  "
                f"({100 * no_card / detailed:.0f}% of frames carrying detail)"
            )
        if clipped:
            print(f"frames where a card ran off the edge: {clipped}")

        latencies = [e.latency_ms for e in events if e.latency_ms]
        if latencies:
            print(
                f"latency   median {statistics.median(latencies):.0f} ms   "
                f"p90 {_percentile(latencies, 0.9):.0f} ms   "
                f"max {max(latencies):.0f} ms"
            )

        # --- accuracy ----------------------------------------------------------
        confirmed = [e for e in events if e.confirmed_card_id is not None]
        correct = [e for e in confirmed if e.first_match_card_id == e.confirmed_card_id]
        print(f"\ncards added: {len(confirmed)}")
        if confirmed:
            print(f"  first match was the one kept: {len(correct)}/{len(confirmed)}")
            print(f"  frames analysed per card added: {len(events) / len(confirmed):.1f}")
            by_method = Counter(e.method for e in confirmed)
            print(
                "  by signal: "
                + ", ".join(f"{name}={count}" for name, count in by_method.most_common())
            )

        wrong = [e for e in confirmed if e.first_match_card_id != e.confirmed_card_id]
        if wrong:
            print("\ncards where the first match was NOT what you kept:")
            for event in wrong:
                proposed = (
                    db.get(Card, event.first_match_card_id) if event.first_match_card_id else None
                )
                kept = db.get(Card, event.confirmed_card_id)
                proposed_label = (
                    f"{proposed.set_code}/{proposed.collector_number}" if proposed else "nothing"
                )
                print(
                    f"  proposed {proposed_label:<12}"
                    f" kept {kept.set_code}/{kept.collector_number} {kept.name[:30]}"
                )

        db.rollback()
    return 0


if __name__ == "__main__":
    sys.exit(main())
