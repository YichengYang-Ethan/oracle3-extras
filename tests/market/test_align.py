"""Outcome alignment, on payloads copied from real Kalshi and Polymarket markets."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from oracle3_extras.market.align import align_kalshi_polymarket, words
from tests.factories import gamma_market, kalshi_market


def nhl(yes: str = 'Dallas', ticker: str = 'KXNHLGAME-26OCT10DALPIT-DAL', **kw):
    return kalshi_market(ticker, yes, occurrence='2026-10-11T02:00:00Z', **kw)


STARS_PENGUINS = gamma_market(
    '4527234',
    'Stars vs. Penguins',
    ['Stars', 'Penguins'],
    slug='nhl-dal-pit-2026-10-10',
)


# ── Head to head ─────────────────────────────────────────────────────────


def test_team_code_aligns_city_with_nickname() -> None:
    result = align_kalshi_polymarket(nhl(), STARS_PENGUINS)
    assert (result.relation, result.outcome_index) == ('same_event', 0)
    assert result.evidence == ('team code',)


def test_second_outcome_is_a_complement() -> None:
    result = align_kalshi_polymarket(
        nhl('Pittsburgh', 'KXNHLGAME-26OCT10DALPIT-PIT'), STARS_PENGUINS
    )
    assert (result.relation, result.outcome_index) == ('complement', 1)


def test_name_and_code_agree() -> None:
    kalshi = kalshi_market(
        'KXAHLGAME-26OCT091900MANGRA-MAN',
        'Manitoba Moose',
        occurrence='2026-10-10T02:00:00Z',
    )
    poly = gamma_market(
        '5237511',
        'AHL: Manitoba Moose vs. Grand\xa0Rapids\xa0Griffins',
        ['Manitoba Moose', 'Grand\xa0Rapids\xa0Griffins'],
        slug='ahl-man-gra-2026-10-09',
        start='2026-10-09 23:00:00+00',
    )
    result = align_kalshi_polymarket(kalshi, poly)
    assert result.evidence == ('name', 'team code')
    assert result.relation == 'same_event'


def test_abbreviated_name_matches() -> None:
    kalshi = kalshi_market(
        'KXNCAAFGAME-26OCT14KENNMOSU-KENN',
        'Kennesaw St.',
        occurrence='2026-10-15T02:30:00Z',
    )
    poly = gamma_market(
        '5347877',
        'Kennesaw State vs. Missouri State',
        ['Kennesaw State', 'Missouri State'],
        slug='cfb-kenest-msrst-2026-10-14',
        start='2026-10-14 23:30:00+00',
    )
    result = align_kalshi_polymarket(kalshi, poly)
    assert (result.outcome_index, result.evidence) == (0, ('name',))


def test_exact_name_beats_a_longer_one() -> None:
    kalshi = kalshi_market(
        'KXNCAAFGAME-26OCT17KUKSU-KU', 'Kansas', occurrence='2026-10-17T20:00:00Z'
    )
    poly = gamma_market(
        '5348755',
        'Kansas vs. Kansas State',
        ['Kansas', 'Kansas State'],
        slug='cfb-kan-kanst-2026-10-17',
        start='2026-10-17 19:00:00+00',
    )
    assert align_kalshi_polymarket(kalshi, poly).outcome_index == 0


def test_name_and_code_disagree_is_rejected() -> None:
    swapped = gamma_market(
        '1',
        'Penguins vs. Stars',
        ['Penguins', 'Dallas Stars'],
        slug='nhl-dal-pit-2026-10-10',
    )
    result = align_kalshi_polymarket(nhl(), swapped)
    assert not result.aligned
    assert 'different outcomes' in result.problem


def test_unknown_outcome_is_rejected() -> None:
    poly = gamma_market(
        '1', 'Stars vs. Penguins', ['Stars', 'Penguins'], slug='nhl-x-y-2026-10-10'
    )
    result = align_kalshi_polymarket(nhl(), poly)
    assert not result.aligned and 'could not tell' in result.problem


# ── Binary Yes/No markets ────────────────────────────────────────────────


def mls(code: str = 'DAL', yes: str = 'Dallas'):
    return kalshi_market(
        f'KXMLSGAME-26OCT10CLTDAL-{code}', yes, occurrence='2026-10-11T02:30:00Z'
    )


def poly_mls(suffix: str, question: str, group: str | None):
    return gamma_market(
        '5036171',
        question,
        ['Yes', 'No'],
        slug=f'mls-clt-dal-2026-10-10-{suffix}',
        group=group,
        start='2026-10-10 23:30:00+00',
        event_title='Charlotte FC vs. FC Dallas',
    )


def test_binary_same_team() -> None:
    result = align_kalshi_polymarket(
        mls(), poly_mls('dal', 'Will FC Dallas win on 2026-10-10?', 'FC Dallas')
    )
    assert (result.relation, result.kind, result.evidence) == (
        'same_event',
        'binary',
        ('name', 'team code'),
    )


def test_binary_other_team_is_rejected() -> None:
    result = align_kalshi_polymarket(
        mls(), poly_mls('clt', 'Will Charlotte FC win on 2026-10-10?', 'Charlotte FC')
    )
    assert not result.aligned and 'other team' in result.problem


def test_binary_draw() -> None:
    draw = poly_mls(
        'draw',
        'Will Charlotte FC vs. FC Dallas end in a draw?',
        'Draw (Charlotte FC vs. FC Dallas)',
    )
    assert align_kalshi_polymarket(mls('TIE', 'Tie'), draw).evidence == (
        'draw on both venues',
    )
    assert not align_kalshi_polymarket(mls(), draw).aligned


def test_binary_with_foreign_letters() -> None:
    kalshi = kalshi_market(
        'KXELITESERIENGAME-26OCT11LSKMFK-LSK',
        'Lillestroem',
        occurrence='2026-10-11T18:00:00Z',
    )
    poly = gamma_market(
        '5048812',
        'Will Lillestrøm SK win on 2026-10-11?',
        ['Yes', 'No'],
        slug='nor-lil-mol-2026-10-11-lil',
        group='Lillestrøm SK',
        start='2026-10-11 16:00:00+00',
        event_title='Lillestrøm SK vs. Molde FK',
    )
    assert align_kalshi_polymarket(kalshi, poly).evidence == ('name',)


def test_non_sports_binary_is_not_guessed() -> None:
    kalshi = kalshi_market(
        'KXFED-26DEC-T4.25', 'Above 4.25%', occurrence='2026-12-10T19:00:00Z'
    )
    poly = gamma_market(
        '1',
        'Fed rate above 4.25% after December?',
        ['Yes', 'No'],
        slug='fed',
        sports_type=None,
    )
    assert not align_kalshi_polymarket(kalshi, poly).aligned


# ── Totals ───────────────────────────────────────────────────────────────


def total(line: float = 49.5, strike: str = 'greater'):
    return kalshi_market(
        'KXNCAAFTOTAL-26OCT10ODUAPP-50',
        f'Over {line} points',
        title=f'Full Game: Over {line} points scored',
        strike_type=strike,
        floor_strike=line,
        occurrence='2026-10-10T20:00:00Z',
    )


def poly_total(line: float = 49.5, question: str | None = None):
    return gamma_market(
        '5097184',
        question or f'Old Dominion vs. Appalachian State: O/U {line}',
        ['Over', 'Under'],
        slug=f'cfb-old-applst-2026-10-10-total-{str(line).replace(".", "pt")}',
        sports_type='totals',
        line=line,
        start='2026-10-10 17:00:00+00',
    )


def test_totals_same_line() -> None:
    result = align_kalshi_polymarket(total(), poly_total())
    assert (result.relation, result.evidence) == (
        'same_event',
        ('line 49.5 on both venues', 'over'),
    )


def test_totals_under_is_the_second_outcome() -> None:
    assert (
        align_kalshi_polymarket(total(strike='less'), poly_total()).relation
        == 'complement'
    )


def test_totals_different_lines_are_rejected() -> None:
    result = align_kalshi_polymarket(total(49.5), poly_total(50.5))
    assert result.problem == 'different lines: Kalshi 49.5, Polymarket 50.5'


def test_first_half_never_matches_full_game() -> None:
    result = align_kalshi_polymarket(
        total(), poly_total(question='Old Dominion vs. Appalachian State: 1H O/U 49.5')
    )
    assert 'different periods' in result.problem


def test_spreads_are_not_aligned_yet() -> None:
    poly = gamma_market(
        '1',
        'Spread: Stars (-1.5)',
        ['Stars', 'Penguins'],
        slug='nhl-dal-pit-2026-10-10-spread',
        sports_type='spreads',
        line=-1.5,
    )
    assert align_kalshi_polymarket(nhl(), poly).kind == 'spreads'
    assert not align_kalshi_polymarket(nhl(), poly).aligned


# ── Schedule ─────────────────────────────────────────────────────────────


def test_games_days_apart_are_rejected() -> None:
    later = dict(STARS_PENGUINS, gameStartTime='2026-10-14 23:00:00+00')
    assert align_kalshi_polymarket(nhl(), later).problem.startswith('different dates')


def test_ticker_start_time_separates_consecutive_days() -> None:
    kalshi = kalshi_market(
        'KXKBOGAME-26OCT090100LGLOT-LG', 'LG Twins', occurrence='2026-10-09T09:00:00Z'
    )
    tomorrow = gamma_market(
        '1',
        'KBO: LG Twins vs. Lotte Giants',
        ['LG Twins', 'Lotte Giants'],
        slug='kbo-lg-lot-2026-10-10',
        start='2026-10-10 05:00:00+00',
    )
    assert align_kalshi_polymarket(kalshi, tomorrow).problem.startswith(
        'different start times'
    )


def test_utc_dated_slug_is_fine() -> None:
    # Thursday-night game: Kalshi dates it Oct 8 (Eastern), Polymarket starts 00:15Z Oct 9.
    kalshi = kalshi_market(
        'KXNFLGAME-26OCT08TBDAL-TB', 'Tampa Bay', occurrence='2026-10-09T03:15:00Z'
    )
    poly = gamma_market(
        '1',
        'Buccaneers vs. Cowboys',
        ['Buccaneers', 'Cowboys'],
        slug='nfl-tb-dal-2026-10-09',
        start='2026-10-09 00:15:00+00',
    )
    result = align_kalshi_polymarket(kalshi, poly)
    assert result.aligned and result.warnings == ()


def test_rescheduled_game_is_flagged() -> None:
    kalshi = kalshi_market(
        'KXKBOGAME-26OCT090100LGLOT-LG', 'LG Twins', occurrence='2026-10-09T09:00:00Z'
    )
    poly = gamma_market(
        '1',
        'KBO: LG Twins vs. Lotte Giants',
        ['LG Twins', 'Lotte Giants'],
        slug='kbo-lg-lot-2026-08-28',
        start='2026-10-09 05:00:00+00',
    )
    result = align_kalshi_polymarket(kalshi, poly)
    assert result.aligned
    assert result.warnings == (
        'rescheduled: Polymarket first listed this game for 2026-08-28',
    )


@pytest.mark.parametrize(
    ('rules', 'aligned'),
    [
        (
            'If Hynek Barton wins the Barton vs Samuel professional tennis match in the 2026 Villena Round of 32 after a ball has been played, then the market resolves to Yes.',
            True,
        ),
        (
            'If Arkansas wins the Arkansas vs Vanderbilt college football game originally scheduled for Oct 16, 2026, then the market resolves to Yes.',
            False,
        ),
    ],
)
def test_postponed_one_day_depends_on_dated_rules(rules: str, aligned: bool) -> None:
    kalshi = kalshi_market(
        'KXATPCHALLENGERMATCH-26OCT06BARSAM-BAR',
        'Hynek Barton',
        occurrence='2026-10-06T15:30:00Z',
        rules=rules,
    )
    poly = gamma_market(
        '5331928',
        'Villena: Hynek Barton vs Toby Samuel',
        ['Hynek Barton', 'Toby Samuel'],
        slug='atp-barton-samuel-2026-10-07',
        start='2026-10-07 09:00:00+00',
    )
    result = align_kalshi_polymarket(kalshi, poly)
    assert result.aligned is aligned
    if aligned:
        assert 'postponed' in result.warnings[0]


def test_words_drop_noise_and_expand_abbreviations() -> None:
    assert words('CA Boca Juniors') == {'boca', 'juniors'}
    assert words('Grand\xa0Rapids') == {'grand', 'rapids'}


def test_rain_delayed_tennis_match_is_kept_with_a_warning(monkeypatch) -> None:
    from oracle3_extras.market import align

    monkeypatch.setattr(
        align, '_now', lambda: datetime(2026, 10, 7, 20, tzinfo=timezone.utc)
    )
    kalshi = kalshi_market(
        'KXATPMATCH-26OCT06BORDIA-BOR',
        'Nuno Borges',
        occurrence='2026-10-06T07:00:00Z',
        rules='If Nuno Borges wins the Borges vs Diaz Acosta professional tennis match '
        'in the 2026 ATP Shanghai Round Of 128 after a ball has been played, then the '
        'market resolves to Yes.',
        close_time='2026-10-20T04:00:00Z',
    )
    poly = gamma_market(
        '5320334',
        'Shanghai Rolex Masters: Nuno Borges vs Facundo Diaz Acosta',
        ['Nuno Borges', 'Facundo Diaz Acosta'],
        slug='atp-borges-diazaco-2026-10-08',
        start='2026-10-08 04:00:00+00',
    )
    result = align_kalshi_polymarket(kalshi, poly)
    assert result.aligned, result.problem
    assert result.warnings[0].startswith(
        'postponed: Kalshi scheduled it for 2026-10-06'
    )
    # The same pair before the original date has passed is still two different days.
    monkeypatch.setattr(
        align, '_now', lambda: datetime(2026, 10, 6, 8, tzinfo=timezone.utc)
    )
    assert align_kalshi_polymarket(kalshi, poly).problem.startswith('different dates')
