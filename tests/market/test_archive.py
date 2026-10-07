from __future__ import annotations

from datetime import datetime, timedelta, timezone

from oracle3.market.relations import MarketRelation

from oracle3_extras.market.archive import (
    event_time,
    load_archive,
    started_between,
    update_archive,
)

NOW = datetime(2026, 10, 7, 12, tzinfo=timezone.utc)


def relation(
    rid: str, start: datetime | None, confidence: float = 0.9
) -> MarketRelation:
    return MarketRelation(
        relation_id=rid,
        market_a={'venue': 'kalshi', 'market_id': rid},
        market_b={
            'venue': 'polymarket',
            'market_id': '1',
            'game_start': start.isoformat() if start else '',
        },
        spread_type='same_event',
        confidence=confidence,
    )


def test_update_keeps_the_newest_copy_and_drops_old_events(tmp_path) -> None:
    path = tmp_path / 'archive.jsonl.gz'
    first = update_archive(
        path, [relation('a', NOW - timedelta(days=20)), relation('b', NOW)], now=NOW
    )
    assert first == {'added': 2, 'updated': 0, 'dropped': 1, 'total': 1}
    counts = update_archive(path, [relation('b', NOW, confidence=0.99)], now=NOW)
    assert counts == {'added': 0, 'updated': 1, 'dropped': 0, 'total': 1}
    [only] = load_archive(path)
    assert only.relation_id == 'b' and only.confidence == 0.99


def test_relations_without_a_start_age_out_by_archive_date(tmp_path) -> None:
    path = tmp_path / 'archive.jsonl.gz'
    update_archive(path, [relation('x', None)], now=NOW - timedelta(days=30))
    assert update_archive(path, [], now=NOW)['dropped'] == 1


def test_started_between_and_event_time() -> None:
    early, late = relation('e', NOW - timedelta(hours=10)), relation('l', NOW)
    assert event_time(late) == NOW
    picked = started_between([early, late], NOW - timedelta(hours=12), NOW)
    assert [r.relation_id for r in picked] == ['e']
