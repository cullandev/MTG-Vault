"""Rebuild the whole synergy picture: tags, edges, cores, persisted."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import delete, select
from sqlalchemy.orm import Session as DbSession

from app.models import CardTag, CollectionItem, SynergyCore, SynergyCoreCard
from app.services.synergy import clustering, graph
from app.services.synergy.patterns import default_table


@dataclass
class RebuildReport:
    """What one rebuild produced."""

    pool_size: int = 0
    tagged: int = 0
    edges: int = 0
    cores: int = 0


def rebuild(db: DbSession) -> RebuildReport:
    """Recompute tags, edges and cores over the vault's distinct cards."""
    report = RebuildReport()
    pool = sorted(set(db.scalars(select(CollectionItem.oracle_id).distinct())))
    report.pool_size = len(pool)

    table = default_table()
    tags = graph.tag_pool(db, pool, table)
    db.execute(delete(CardTag))
    for tag, oracle_ids in tags.items():
        for oracle_id in oracle_ids:
            db.add(CardTag(oracle_id=oracle_id, tag=tag))
            report.tagged += 1

    edges = graph.build_edges(db, pool, table=table)
    report.edges = graph.store_edges(db, edges)

    cores = clustering.find_cores(db, edges, pool=pool)
    db.execute(delete(SynergyCoreCard))
    db.execute(delete(SynergyCore))
    for core in cores:
        row = SynergyCore(
            color_identity=core.color_identity,
            color_identity_mask=core.color_identity_mask,
            theme_name=core.theme_name,
            card_count=len(core.oracle_ids),
            density=core.density,
            buildability=core.buildability,
            combined_score=core.combined_score,
        )
        db.add(row)
        db.flush()
        for oracle_id in core.oracle_ids:
            db.add(
                SynergyCoreCard(
                    core_id=row.id,
                    oracle_id=oracle_id,
                    centrality=core.centrality.get(oracle_id, 0.0),
                )
            )
        report.cores += 1
    db.flush()
    return report


def core_from_row(db: DbSession, core_id: int) -> clustering.Core | None:
    """Rehydrate a stored core for suggestion and assembly."""
    row = db.get(SynergyCore, core_id)
    if row is None:
        return None
    members = (
        db.execute(
            select(SynergyCoreCard.oracle_id, SynergyCoreCard.centrality).where(
                SynergyCoreCard.core_id == core_id
            )
        )
        .tuples()
        .all()
    )
    return clustering.Core(
        oracle_ids=[oracle_id for oracle_id, _c in members],
        centrality=dict(members),
        theme_name=row.theme_name,
        color_identity=row.color_identity,
        color_identity_mask=row.color_identity_mask,
        density=row.density,
        buildability=row.buildability,
    )
