from __future__ import annotations

import json

from click.testing import CliRunner
from oracle3.market.relations import RelationStore

from oracle3_extras import venues
from oracle3_extras.cli import cli
from oracle3_extras.market.kairos import DATA_API
from tests.factories import gamma_market, kairos_page, kairos_pair, kalshi_market

K = kalshi_market(
    'KXNHLGAME-26OCT10DALPIT-DAL', 'Dallas', yes_bid='0.3800', yes_ask='0.4000'
)
P = gamma_market(
    '1',
    'Stars vs. Penguins',
    ['Stars', 'Penguins'],
    slug='nhl-dal-pit-2026-10-10',
    best_bid=0.55,
    best_ask=0.57,
    tokens=('p1a', 'p1b'),
)
BAD_K = kalshi_market('KXNHLGAME-26OCT10AAABBB-AAA', 'Aaa', status='finalized')
BAD_P = gamma_market('2', 'A vs. B', ['A', 'B'], slug='nhl-aaa-bbb-2026-10-10')


def route(http) -> None:
    http.get(
        f'{DATA_API}/matched-markets',
        kairos_page(
            [
                kairos_pair(K['ticker'], 'Dallas wins', '1', 'Stars vs. Penguins'),
                kairos_pair(BAD_K['ticker'], 'Aaa wins', '2', 'A vs. B'),
            ]
        ),
    )
    http.get(f'{venues.KALSHI_API}/markets', {'markets': [K, BAD_K]})
    http.get(
        f'{venues.KALSHI_API}/series/KXNHLGAME',
        {'series': {'fee_type': 'quadratic', 'fee_multiplier': 1}},
    )
    http.get(f'{venues.GAMMA_API}/markets', [P, BAD_P])


def invoke(*args: str):
    result = CliRunner().invoke(cli, ['--quiet', *args])
    return result, (json.loads(result.stdout) if result.stdout.strip() else None)


def test_pairs_lists_aligned_and_rejected(http) -> None:
    route(http)
    result, out = invoke('kairos', 'pairs', '--rejected')
    assert result.exit_code == 0, result.output
    assert out['summary']['aligned'] == 1
    [row] = out['relations']
    assert row['polymarket']['matching_outcome'] == 'Stars'
    assert row['evidence'] == ['team code']
    assert out['rejected'][0]['reason'] == 'Kalshi market is finalized'


def test_sync_writes_an_oracle3_store(http, tmp_path) -> None:
    route(http)
    store = tmp_path / 'relations.json'
    result, out = invoke('kairos', 'sync', '--store', str(store))
    assert result.exit_code == 0, result.output
    assert out['written'] == {'added': 1, 'updated': 0, 'removed': 0, 'kept': 0}
    assert [r.relation_id for r in RelationStore(store).list()] == [
        'kairos:KXNHLGAME-26OCT10DALPIT-DAL:1'
    ]


def test_sync_dry_run_writes_nothing(http, tmp_path) -> None:
    route(http)
    store = tmp_path / 'relations.json'
    result, out = invoke('kairos', 'sync', '--store', str(store), '--dry-run')
    assert result.exit_code == 0 and out['written'] is None and not store.exists()


def test_kairos_scan_top_of_book(http) -> None:
    route(http)
    result, out = invoke('kairos', 'scan', '--no-depth')
    assert result.exit_code == 0, result.output
    assert out['summary']['opportunities'] == 1
    assert out['opportunities'][0]['basket'] == 'YES on A + NO on B'
    assert out['catalog']['aligned'] == 1


def test_scan_reads_the_store(http, tmp_path) -> None:
    route(http)
    store = tmp_path / 'relations.json'
    invoke('kairos', 'sync', '--store', str(store))
    result, out = invoke(
        'scan', '--store', str(store), '--source', 'kairos', '--no-depth'
    )
    assert result.exit_code == 0, result.output
    assert out['summary']['relations'] == 1 and out['summary']['opportunities'] == 1


def test_scan_of_a_missing_store_is_empty(tmp_path) -> None:
    result, out = invoke('scan', '--store', str(tmp_path / 'none.json'))
    assert result.exit_code == 0 and out['summary']['relations'] == 0


def test_api_failure_is_a_json_error(http) -> None:
    http.get(
        f'{DATA_API}/matched-markets',
        (503, {'detail': 'Correlation store unavailable'}),
    )
    result, out = invoke('kairos', 'pairs')
    assert result.exit_code == 1
    assert out['error']['code'] == 'API_ERROR'
