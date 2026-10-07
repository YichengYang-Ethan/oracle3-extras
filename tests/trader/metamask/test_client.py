from __future__ import annotations

from decimal import Decimal

import pytest

from oracle3_extras.trader.metamask import (
    AgentWalletClient,
    AgentWalletError,
    ApprovalRequiredError,
    Attribution,
    CLINotInstalledError,
    CLIProtocolError,
    CLITimeoutError,
    GeoblockedError,
    InsufficientBalanceError,
    OrderRejectedError,
    SessionError,
    metadata_bytes32,
)
from oracle3_extras.trader.metamask.client import _parse_envelope
from tests.trader.metamask.support import err, ok

PLACE_OK = ok(
    'place',
    {
        'chainId': 137,
        'negRisk': False,
        'response': {
            'orderId': '0xabc',
            'status': 'matched',
            'success': True,
            'makingAmount': '5.5',
            'takingAmount': '10',
            'transactionHashes': ['0xtx'],
        },
    },
)


def test_metadata_bytes32_is_readable_ascii() -> None:
    value = metadata_bytes32('oracle3')
    assert value == '0x6f7261636c6533' + '0' * 50
    assert len(value) == 66
    with pytest.raises(ValueError):
        metadata_bytes32('')
    with pytest.raises(ValueError):
        metadata_bytes32('x' * 33)


def test_attribution_opt_out_from_env() -> None:
    assert Attribution.from_env({}).enabled
    assert not Attribution.from_env({'ORACLE3_ATTRIBUTION': 'off'}).enabled
    off = Attribution(enabled=False)
    assert off.env() == {}
    assert off.metadata is None


def test_quote_parses_result_and_passes_flags(fake_mm) -> None:
    fake_mm.respond(
        'predict quote',
        ok(
            'quote',
            {
                'tokenId': 'T1',
                'book': {},
                'quote': {
                    'side': 'buy',
                    'requestedSize': 10,
                    'filledSize': 10,
                    'remainingSize': 0,
                    'cost': 5.5,
                    'proceeds': 0,
                    'averagePrice': 0.55,
                    'worstPrice': 0.56,
                    'fullyFilled': True,
                },
            },
        ),
    )
    quote = fake_mm.client().quote(
        'T1', 'buy', Decimal('10'), limit_price=Decimal('0.6')
    )
    assert quote.average_price == Decimal('0.55')
    assert quote.cost == Decimal('5.5')
    assert quote.fully_filled
    argv = fake_mm.calls('predict quote')[-1]['argv']
    assert argv[-1] == '--json'
    assert argv[argv.index('--limit-price') + 1] == '0.6'
    assert argv[argv.index('--size') + 1] == '10'


def test_place_sends_attribution_env_but_no_metadata_when_flag_missing(fake_mm) -> None:
    fake_mm.respond('predict place', PLACE_OK).help(
        'predict place', 'Usage: mm predict place --token-id ...'
    )
    result = fake_mm.client().place('T1', 'buy', Decimal('10'), Decimal('0.56'))
    assert result.matched
    assert result.order_id == '0xabc'
    assert result.making_amount == Decimal('5.5')
    call = fake_mm.calls('predict place')[-1]
    assert '--metadata' not in call['argv']
    assert call['argv'][call['argv'].index('--order-type') + 1] == 'FOK'
    assert call['env']['MM_INTEGRATION_ID'] == 'oracle3'


def test_place_sends_metadata_when_cli_supports_it(fake_mm) -> None:
    fake_mm.respond('predict place', PLACE_OK).help(
        'predict place', '  --metadata=<value>  bytes32 order metadata'
    )
    fake_mm.client().place('T1', 'buy', Decimal('10'), Decimal('0.56'))
    argv = fake_mm.calls('predict place')[-1]['argv']
    assert argv[argv.index('--metadata') + 1] == metadata_bytes32('oracle3')


def test_opted_out_client_sends_nothing(fake_mm) -> None:
    fake_mm.respond('predict place', PLACE_OK).help('predict place', '--metadata')
    fake_mm.client(attribution=Attribution(enabled=False)).place(
        'T1', 'buy', Decimal('10'), Decimal('0.56')
    )
    call = fake_mm.calls('predict place')[-1]
    assert '--metadata' not in call['argv']
    assert call['env']['MM_INTEGRATION_ID'] is None


@pytest.mark.parametrize(
    ('code', 'exc_type'),
    [
        ('PREDICT_GEOBLOCKED', GeoblockedError),
        ('PREDICT_INSUFFICIENT_BALANCE', InsufficientBalanceError),
        ('PREDICT_ORDER_NOT_FILLED', OrderRejectedError),
        ('NOT_INITIALIZED', SessionError),
        ('AWAITING_MFA', ApprovalRequiredError),
        ('POLICY_VIOLATION', ApprovalRequiredError),
        ('SOMETHING_ELSE', AgentWalletError),
    ],
)
def test_error_codes_map_to_typed_errors(fake_mm, code, exc_type) -> None:
    fake_mm.respond('predict place', err(code, 'nope', 'try again'))
    with pytest.raises(exc_type) as info:
        fake_mm.client().place('T1', 'buy', Decimal('1'), Decimal('0.5'))
    assert info.value.code == code
    assert info.value.hint == 'try again'


def test_error_envelope_on_stderr_is_read(fake_mm) -> None:
    reply = err('PREDICT_GEOBLOCKED')
    reply['__stderr__'] = True
    fake_mm.respond('predict geoblock', reply)
    with pytest.raises(GeoblockedError):
        fake_mm.client().geoblock()


def test_missing_cli_raises(tmp_path) -> None:
    client = AgentWalletClient(executable=str(tmp_path / 'no-such-mm'))
    with pytest.raises(CLINotInstalledError) as info:
        client.doctor()
    assert 'npm install -g @metamask/agent-wallet' in info.value.hint


def test_non_json_output_is_a_protocol_error(fake_mm) -> None:
    fake_mm.respond('doctor', {'__raw__': 'segfault', '__exit__': 2})
    with pytest.raises(CLIProtocolError):
        fake_mm.client().doctor()


def test_timeout(fake_mm) -> None:
    fake_mm.respond('doctor', {'__sleep__': 3, 'ok': True, 'data': {}})
    with pytest.raises(CLITimeoutError):
        fake_mm.client(timeout=0.5).doctor()


@pytest.mark.parametrize(
    ('raw', 'expected'),
    [('12500000', Decimal('12.5')), ('3.25', Decimal('3.25')), ('0', Decimal('0'))],
)
def test_balance_handles_base_units_and_decimals(fake_mm, raw, expected) -> None:
    fake_mm.respond(
        'predict balance',
        ok(
            'balance', {'assetType': 'COLLATERAL', 'balanceAllowance': {'balance': raw}}
        ),
    )
    assert fake_mm.client().balance() == expected
    assert '--sync' in fake_mm.calls('predict balance')[-1]['argv']


def test_mode_and_geoblock(fake_mm) -> None:
    fake_mm.respond('predict mode', ok('mode', {'mode': 'testnet', 'chainId': 80002}))
    fake_mm.respond(
        'predict geoblock',
        ok(
            'geoblock',
            {'blocked': True, 'country': 'US', 'region': 'IL', 'ip': '1.2.3.4'},
        ),
    )
    client = fake_mm.client()
    assert client.predict_mode() == 'testnet'
    geo = client.geoblock()
    assert geo.blocked and geo.country == 'US'
    client.set_predict_mode('testnet')
    assert fake_mm.calls('predict mode')[-1]['argv'][:3] == [
        'predict',
        'mode',
        'testnet',
    ]
    with pytest.raises(ValueError):
        client.set_predict_mode('devnet')


def test_positions_parse_polymarket_fields(fake_mm) -> None:
    fake_mm.respond(
        'predict positions',
        ok(
            'positions',
            {
                'positions': [
                    {
                        'asset': 'T1',
                        'size': 10,
                        'avgPrice': 0.55,
                        'curPrice': 0.6,
                        'conditionId': '0xc',
                        'outcome': 'Yes',
                        'title': 'Fed cut?',
                        'redeemable': False,
                    },
                    {'tokenId': 'T2', 'size': '2.5', 'avgPrice': '0.4'},
                    {'size': 1},
                ]
            },
        ),
    )
    positions = fake_mm.client().positions()
    assert [p.token_id for p in positions] == ['T1', 'T2']
    assert positions[0].avg_price == Decimal('0.55')
    assert positions[1].size == Decimal('2.5')


def test_validation_happens_before_any_call(fake_mm) -> None:
    client = fake_mm.client()
    with pytest.raises(ValueError):
        client.place('T1', 'hold', Decimal('1'), Decimal('0.5'))
    with pytest.raises(ValueError):
        client.place('T1', 'buy', Decimal('1'), Decimal('0.5'), order_type='IOC')
    with pytest.raises(ValueError):
        client.redeem()
    assert fake_mm.calls() == []


def test_ndjson_summary_and_error_lines() -> None:
    assert _parse_envelope('{"item": 1}\n{"_summary": {"n": 2}}') == {
        'ok': True,
        'data': {'n': 2},
    }
    assert _parse_envelope('{"item": 1}\n{"_error": {"code": "X"}}') == {
        'ok': False,
        'error': {'code': 'X'},
    }
    assert _parse_envelope('not json') is None
    assert _parse_envelope('') is None
