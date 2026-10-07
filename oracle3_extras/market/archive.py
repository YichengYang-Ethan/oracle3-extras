"""A rolling archive of relations, kept after their source stops listing them.

Kairos drops a pair from its catalog once the markets expire, but a pair is
most interesting afterwards: how the two prices moved during the game, and
whether both venues settled it the same way. :func:`update_archive` keeps the
latest copy of every relation it is given in one gzipped JSON-lines file and
forgets relations whose event is older than ``keep_days``. Rows are oracle3
``MarketRelation`` dicts, so :func:`load_archive` returns relations oracle3
understands.
"""

from __future__ import annotations

import gzip
import json
import os
from collections.abc import Iterable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from oracle3.market.relations import MarketRelation

from oracle3_extras.market.align import utc_time

__all__ = ['event_time', 'load_archive', 'started_between', 'update_archive']


def event_time(relation: MarketRelation | dict[str, Any]) -> datetime | None:
    """When the relation's event starts: a game start on either side, else its expiry."""
    row = relation.to_dict() if isinstance(relation, MarketRelation) else relation
    for market in (row.get('market_b') or {}, row.get('market_a') or {}):
        start = utc_time(market.get('game_start'))
        if start:
            return start
    for market in (row.get('market_b') or {}, row.get('market_a') or {}):
        day = utc_time(market.get('event_date'))
        if day:
            return day
    return utc_time(row.get('valid_until'))


def started_between(
    relations: Iterable[MarketRelation], since: datetime, until: datetime
) -> list[MarketRelation]:
    """Relations whose event started in ``[since, until)``."""
    chosen = []
    for relation in relations:
        start = event_time(relation)
        if start is not None and since <= start < until:
            chosen.append(relation)
    return chosen


def _read(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with gzip.open(path, 'rt', encoding='utf-8') as handle:
        return [json.loads(line) for line in handle if line.strip()]


def load_archive(path: Path | str) -> list[MarketRelation]:
    """Every relation in the archive, as oracle3 ``MarketRelation`` objects."""
    return [MarketRelation.from_dict(row) for row in _read(Path(path))]


def update_archive(
    path: Path | str,
    relations: Iterable[MarketRelation],
    *,
    keep_days: float = 14,
    now: datetime | None = None,
) -> dict[str, int]:
    """Add or refresh ``relations`` and drop those whose event is too old.

    The newest copy of a relation replaces the archived one. A relation is
    dropped once its event started more than ``keep_days`` ago (or, without a
    known start, once it was archived that long ago). The file is rewritten
    atomically.
    """
    path = Path(path)
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=keep_days)
    rows = {str(row.get('relation_id')): row for row in _read(path)}
    counts = {'added': 0, 'updated': 0, 'dropped': 0}
    stamp = now.isoformat(timespec='seconds')
    for relation in relations:
        rid = relation.relation_id
        counts['updated' if rid in rows else 'added'] += 1
        rows[rid] = {**relation.to_dict(), 'archived_at': stamp}
    kept = []
    for row in rows.values():
        moment = event_time(row) or utc_time(row.get('archived_at'))
        if moment is not None and moment < cutoff:
            counts['dropped'] += 1
            continue
        kept.append(row)
    counts['total'] = len(kept)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.tmp')
    with gzip.open(tmp, 'wt', encoding='utf-8') as handle:
        for row in kept:
            handle.write(json.dumps(row, default=str) + '\n')
    os.replace(tmp, path)
    return counts
