"""Contract tests against the real services: run with ``pytest --live``.

They catch schema changes in the Kairos, Kalshi and Polymarket APIs that the
offline tests cannot see.
"""

from __future__ import annotations

import pytest

from oracle3_extras.arbitrage import scan_relations
from oracle3_extras.market.kairos import kairos_relations

pytestmark = pytest.mark.live


async def test_kairos_catalog_aligns_and_scans() -> None:
    result = await kairos_relations()
    summary = result.summary()
    assert summary['kalshi_polymarket'] > 0
    assert summary['aligned'] >= 0.8 * summary['kalshi_polymarket']
    report = await scan_relations(result.relations[:200], depth=False)
    assert report.summary()['quoted'] > 0
