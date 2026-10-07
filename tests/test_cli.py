from __future__ import annotations

import json

from click.testing import CliRunner
from conftest import ok

from oracle3_extras.cli import cli


def _invoke(fake_mm, *args: str):
    return CliRunner().invoke(cli, ['metamask', '--mm', str(fake_mm.executable), *args])


def test_doctor_reports_everything(fake_mm) -> None:
    fake_mm.respond(
        'doctor', {'ok': True, 'data': {'authenticated': True, 'initialized': True}}
    )
    fake_mm.respond('predict mode', ok('mode', {'mode': 'testnet'}))
    fake_mm.respond('predict geoblock', ok('geoblock', {'blocked': False}))
    fake_mm.respond(
        'predict balance', ok('balance', {'balanceAllowance': {'balance': '1000000'}})
    )
    fake_mm.help('predict place', 'Usage')
    result = _invoke(fake_mm, 'doctor')
    assert result.exit_code == 0, result.output
    report = json.loads(result.output)
    assert report['ok'] and report['mode'] == 'testnet'
    assert report['balance_pusd'] == '1'
    assert report['metadata_flag_supported'] is False


def test_place_requires_yes(fake_mm) -> None:
    result = _invoke(
        fake_mm,
        'place',
        '--token-id',
        'T1',
        '--side',
        'buy',
        '--size',
        '1',
        '--price',
        '0.5',
    )
    assert result.exit_code != 0
    assert '--yes' in result.output
    assert fake_mm.calls() == []


def test_place_refuses_mainnet_without_flag(fake_mm) -> None:
    fake_mm.respond('predict mode', ok('mode', {'mode': 'mainnet'}))
    result = _invoke(
        fake_mm,
        'place',
        '--token-id',
        'T1',
        '--side',
        'buy',
        '--size',
        '1',
        '--price',
        '0.5',
        '--yes',
    )
    assert result.exit_code != 0
    assert '--allow-mainnet' in result.output
    assert fake_mm.calls('predict place') == []


def test_error_envelope_is_reported_as_json(fake_mm) -> None:
    fake_mm.respond(
        'predict positions',
        {
            'ok': False,
            'error': {
                'code': 'NOT_INITIALIZED',
                'message': 'run mm init',
                'hint': 'mm init',
            },
        },
    )
    result = _invoke(fake_mm, 'positions')
    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload['error']['code'] == 'NOT_INITIALIZED'


def test_doctor_without_cli_exits_nonzero(tmp_path) -> None:
    result = CliRunner().invoke(
        cli, ['metamask', '--mm', str(tmp_path / 'missing'), 'doctor']
    )
    assert result.exit_code == 1
    report = json.loads(result.output)
    assert report['error']['code'] == 'CLI_NOT_INSTALLED'
    assert '[CLI_NOT_INSTALLED]' not in report['error']['message']
