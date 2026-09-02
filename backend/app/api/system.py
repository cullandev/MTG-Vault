"""System endpoints: status, audit log, batch revert, settings."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query
from sqlalchemy import desc, func, select

from app.deps import Config, Db
from app.models import AuditLog, Card, CollectionItem, ImportRun, JobRun, OracleCard
from app.schemas.collection import AuditEntryOut, AuditListResponse
from app.services import audit as audit_service
from app.services import images as image_service
from app.util.pagination import decode_cursor, encode_cursor

router = APIRouter(tags=["system"])


@router.post("/system/backup")
def backup_now() -> dict[str, Any]:
    """Take a verified backup right now -- the thing to press before a risky import.

    Runs inline: ``VACUUM INTO`` on this database takes seconds, and the caller
    wants to know the outcome (path, size, verified) before proceeding.
    """
    from app.jobs.backup import run_backup

    return run_backup().as_dict()


@router.get("/system/status")
def status(db: Db, settings: Config) -> dict[str, Any]:
    """Operational status: data sizes, last job runs, feature availability.

    Authenticated, unlike ``/health``: this one does reveal how big the collection is.
    """
    db_path = settings.db_path
    last_import = db.scalars(
        select(ImportRun).order_by(desc(ImportRun.started_at)).limit(1)
    ).first()
    jobs = db.scalars(select(JobRun).order_by(desc(JobRun.started_at)).limit(20)).all()

    return {
        "database": {
            "path": str(db_path),
            "bytes": db_path.stat().st_size if db_path.exists() else 0,
            "wal_bytes": (
                db_path.with_name(db_path.name + "-wal").stat().st_size
                if db_path.with_name(db_path.name + "-wal").exists()
                else 0
            ),
        },
        "counts": {
            "printings": db.scalar(select(func.count()).select_from(Card)) or 0,
            "oracle_cards": db.scalar(select(func.count()).select_from(OracleCard)) or 0,
            "copies": db.scalar(select(func.count()).select_from(CollectionItem)) or 0,
        },
        "image_cache": {
            "bytes": image_service.cache_size_bytes(db),
            "cap_bytes": settings.image_cache_max_mb * 1024 * 1024,
        },
        "last_import": (
            {
                "kind": last_import.kind,
                "status": last_import.status,
                "started_at": last_import.started_at,
                "finished_at": last_import.finished_at,
                "rows_written": last_import.rows_written,
                "source_updated_at": last_import.source_updated_at,
            }
            if last_import
            else None
        ),
        "jobs": [
            {
                "name": job.job_name,
                "sub_source": job.sub_source,
                "status": job.status,
                "started_at": job.started_at,
                "finished_at": job.finished_at,
            }
            for job in jobs
        ],
        "features": {
            "ai": settings.ai_enabled,
            "edhrec": settings.enable_edhrec,
            "spellbook": settings.enable_spellbook,
            "meta_sources": list(settings.meta_sources),
        },
    }


@router.get("/audit", response_model=AuditListResponse)
def list_audit(
    db: Db,
    entity_type: str | None = None,
    entity_id: str | None = None,
    batch_id: str | None = None,
    since: str | None = None,
    cursor: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
) -> AuditListResponse:
    """Browse the audit log, newest first."""
    statement = select(AuditLog)
    if entity_type:
        statement = statement.where(AuditLog.entity_type == entity_type)
    if entity_id:
        statement = statement.where(AuditLog.entity_id == entity_id)
    if batch_id:
        statement = statement.where(AuditLog.batch_id == batch_id)
    if since:
        statement = statement.where(AuditLog.ts >= since)

    state = decode_cursor(cursor)
    if state is not None:
        statement = statement.where(AuditLog.id < state["key"])

    rows = list(db.scalars(statement.order_by(desc(AuditLog.id)).limit(limit + 1)))
    has_more = len(rows) > limit
    rows = rows[:limit]

    return AuditListResponse(
        items=[
            AuditEntryOut(
                id=row.id,
                ts=row.ts,
                action=row.action,
                entity_type=row.entity_type,
                entity_id=row.entity_id,
                batch_id=row.batch_id,
                source=row.source,
                note=row.note,
                reverted_at=row.reverted_at,
                summary=_summary_of(row),
            )
            for row in rows
        ],
        next_cursor=encode_cursor({"key": rows[-1].id}) if has_more and rows else None,
    )


def _summary_of(row: AuditLog) -> dict[str, Any] | None:
    """Extract the human-readable part of an audit payload, never the whole row dump."""
    payload: Any = row.after_json or row.before_json or {}
    if not isinstance(payload, dict):
        return None
    if "summary" in payload:
        summary = payload["summary"]
        return summary if isinstance(summary, dict) else None
    if "rows" in payload:
        rows = payload["rows"]
        return {"quantity": len(rows) if isinstance(rows, list) else 0}
    keys = ("name", "set_code", "collector_number", "finish", "condition", "lang", "is_proxy")
    return {key: payload[key] for key in keys if key in payload} or None


@router.post("/audit/batches/{batch_id}/revert")
def revert_batch(batch_id: str, db: Db, note: str | None = None) -> dict[str, Any]:
    """Undo every change recorded under one batch id.

    This is the safety net for a bad scan session or a wrong CSV import.
    Deck cache columns are derived from board rows the revert may have touched,
    so any deck named in the batch gets its caches recomputed afterwards.
    """
    result = audit_service.revert_batch(db, batch_id, note=note)
    _refresh_deck_caches_for_batch(db, batch_id)
    return result.as_dict()


def _refresh_deck_caches_for_batch(db: Db, batch_id: str) -> None:
    """Recompute colors/commander caches for decks a reverted batch touched."""
    from app.models import Deck
    from app.services.decks import crud as deck_crud

    deck_ids: set[int] = set()
    entries = db.scalars(select(AuditLog).where(AuditLog.batch_id == batch_id))
    for entry in entries:
        if entry.entity_type == "deck" and entry.entity_id:
            deck_ids.add(int(entry.entity_id))
        elif entry.entity_type == "deck_card":
            for payload in (entry.before_json, entry.after_json):
                for row in (payload or {}).get("rows", []):
                    if "deck_id" in row:
                        deck_ids.add(int(row["deck_id"]))
    for deck_id in deck_ids:
        deck = db.get(Deck, deck_id)
        if deck is not None:
            deck_crud.refresh_caches(db, deck)
