from __future__ import annotations

import json

from click.testing import CliRunner

from oracle3_extras import venues
from oracle3_extras.cli import cli
from oracle3_extras.cli.tickers import parse_companies


def invoke(*args: str):
    result = CliRunner().invoke(cli, ['--quiet', *args])
    return result, (json.loads(result.stdout) if result.stdout.strip() else None)


def test_tickers_command_prints_matches(http) -> None:
    http.get(
        f'{venues.GAMMA_API}/events',
        lambda request: [
            {
                'id': '1',
                'slug': 'rklb-beat',
                'title': 'Will Rocket Lab (RKLB) beat quarterly earnings?',
                'tags': [{'slug': 'finance'}],
                'markets': [],
            },
            {
                'id': '2',
                'slug': 'nvda-up',
                'title': 'NVIDIA (NVDA) Up or Down on October 19?',
                'tags': [{'slug': 'finance'}],
                'markets': [],
            },
        ]
        if request.url.params['tag_slug'] == 'finance'
        else [],
    )
    result, out = invoke('tickers', 'RKLB=Rocket Lab', '--venues', 'polymarket')
    assert result.exit_code == 0, result.output
    [row] = out['events']
    assert (row['ticker'], row['company'], row['event_type']) == (
        'RKLB',
        'Rocket Lab',
        'earnings',
    )
    assert out['summary']['events_searched'] == {'polymarket': 2}


def test_venues_are_checked() -> None:
    result, _ = invoke('tickers', '--venues', 'opinion')
    assert result.exit_code != 0


def test_parse_companies() -> None:
    assert parse_companies(()) is None
    nvda, rklb, meta = parse_companies(
        ('nvda', 'RKLB=Rocket Lab', 'META=Meta|Facebook')
    )
    assert nvda.ticker == 'NVDA' and 'Nvidia' in nvda.names  # from the bundled list
    assert (rklb.name, rklb.names) == ('Rocket Lab', ('Rocket Lab',))
    assert meta.name == 'Meta Platforms' and meta.names == ('Meta', 'Facebook')
