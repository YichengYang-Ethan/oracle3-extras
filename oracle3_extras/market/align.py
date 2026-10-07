"""Line up the outcomes of a Kalshi market and a Polymarket market.

A cross-venue pair can only be traded once its sides are lined up. Kalshi lists
one binary market per outcome ("Dallas wins", "Over 49.5 points"). Polymarket
lists two-outcome markets whose outcomes are ``Yes``/``No``, two team or player
names, or ``Over``/``Under``. If the Kalshi YES outcome is the Polymarket
market's first outcome, the two contracts are the same event, P(A) = P(B). If
it is the second outcome, they are complements, P(A) + P(B) = 1. oracle3
checks both relations natively (``oracle3.arbitrage.check_constraint`` and the
``check_constraint_live`` MCP tool), so no outcome has to be flipped by hand.

The rules are conservative. A pair is aligned only when at least one
independent check identifies the outcome and no check contradicts it:

* names: a word of the Kalshi YES label that names one Polymarket outcome and
  not the other (``Kennesaw St.`` -> ``Kennesaw State``);
* team codes: the code at the end of the Kalshi ticker against the codes in the
  Polymarket slug (``...DALPIT-DAL`` -> ``nhl-dal-pit-...``);
* lines: totals must have the same line on both venues;
* periods: a first-half or single-set market never matches a full-game one;
* schedule: the game must start on the day the Kalshi ticker names (one day
  of slack for time zones), and within three hours of the ticker's start time
  when it has one, which separates games on consecutive days. Polymarket's
  ``gameStartTime`` is used, not its slug date, which goes stale when a game
  is rescheduled.

Everything else comes back with a reason instead of a guess. The rulebooks on
both venues still need a human read: ties, postponements and cancellations can
settle differently.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from difflib import SequenceMatcher
from typing import Any

__all__ = [
    'OutcomeAlignment',
    'align_kalshi_polymarket',
    'align_two_outcome_markets',
    'utc_time',
    'words',
]

#: Largest gap between the start times the two venues publish for one game.
START_TOLERANCE = timedelta(hours=3)
#: Kalshi's expected expiration must fall this long after the start (hours).
EXPIRY_WINDOW = (-3.0, 12.0)

try:
    from zoneinfo import ZoneInfo

    _ET: Any = ZoneInfo('America/New_York')
except Exception:  # noqa: BLE001 - no tz database; Kalshi tickers use US Eastern
    _ET = timezone(timedelta(hours=-4), 'EDT')

_ALIASES = {
    'st': 'state',
    'utd': 'united',
    'intl': 'international',
    'mt': 'mount',
    'ft': 'fort',
    'univ': 'university',
}
_NOISE = frozenset(
    'a ac afc al an and at be bk ca cd cf club cs de del do el fc fk if ik is '
    'la las los match of on rc result sc sd sk sv team the ud v vs will win '
    'wins who game futbol football calcio'.split()
)
_LETTERS = str.maketrans(
    {
        'æ': 'ae',
        'Æ': 'Ae',
        'ø': 'oe',
        'Ø': 'Oe',
        'ß': 'ss',
        'œ': 'oe',
        'Œ': 'Oe',
        'ł': 'l',
        'Ł': 'L',
        'đ': 'd',
        'Đ': 'D',
        'ı': 'i',
        'þ': 'th',
    }
)
_FUZZY_RATIO = 0.85
_PERIODS = (
    ('h1', r'\b(1h|1st half|first half|half ?time)\b'),
    ('h2', r'\b(2h|2nd half|second half)\b'),
    ('q', r'\b([1-4]q|q[1-4]|(1st|2nd|3rd|4th) quarter)\b'),
    ('p', r'\b(1st|2nd|3rd) period\b'),
    ('set', r'\b(set [1-5]|(1st|2nd|3rd) set)\b'),
    ('innings', r'\b(f5|first (5|five) innings|1st inning)\b'),
)
_MONTHS = {
    m: i
    for i, m in enumerate(
        'JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC'.split(), start=1
    )
}
_KALSHI_EVENT = re.compile(
    r'^(?P<yy>\d{2})(?P<mon>[A-Z]{3})(?P<dd>\d{2})(?P<hhmm>\d{4})?(?P<codes>[A-Z0-9]*)$'
)
_LINE = re.compile(r'(?<![\d.])(\d+(?:\.\d+)?)(?![\d.])')


@dataclass(frozen=True)
class OutcomeAlignment:
    """How a Kalshi market's YES outcome maps onto a Polymarket market.

    Attributes:
        relation: ``'same_event'`` when Kalshi YES is Polymarket's first
            outcome, ``'complement'`` when it is the second, ``None`` when the
            pair could not be aligned.
        outcome_index: Index of the Polymarket outcome that pays when Kalshi
            YES pays.
        kind: ``binary``, ``head_to_head``, ``totals``, ``spreads`` or ``other``.
        evidence: The checks that identified the outcome.
        warnings: Things a person should check before trading, such as a game
            that one venue lists as rescheduled.
        problem: Why the pair was not aligned.
    """

    relation: str | None
    outcome_index: int | None
    kind: str
    evidence: tuple[str, ...] = ()
    problem: str = ''
    warnings: tuple[str, ...] = ()

    @property
    def aligned(self) -> bool:
        return self.relation is not None

    @classmethod
    def reject(cls, kind: str, problem: str) -> OutcomeAlignment:
        return cls(relation=None, outcome_index=None, kind=kind, problem=problem)

    @classmethod
    def at(cls, kind: str, index: int, evidence: Sequence[str]) -> OutcomeAlignment:
        return cls(
            relation='same_event' if index == 0 else 'complement',
            outcome_index=index,
            kind=kind,
            evidence=tuple(evidence),
        )

    def warn(self, warnings: Sequence[str]) -> OutcomeAlignment:
        if not warnings or not self.aligned:
            return self
        return OutcomeAlignment(
            self.relation,
            self.outcome_index,
            self.kind,
            self.evidence,
            self.problem,
            self.warnings + tuple(warnings),
        )


# ── Text ─────────────────────────────────────────────────────────────────


def _ascii(text: Any) -> str:
    folded = unicodedata.normalize('NFKD', str(text or '').translate(_LETTERS))
    return folded.encode('ascii', 'ignore').decode().lower()


def words(text: Any) -> frozenset[str]:
    """Distinctive lower-case words of a name, with common abbreviations expanded.

    >>> sorted(words('Kennesaw St.'))
    ['kennesaw', 'state']
    >>> sorted(words('Will Lillestrøm SK win on 2026-10-11?'))
    ['lillestroem']
    """
    out = set()
    for word in re.findall(r'[a-z0-9]+', _ascii(text)):
        word = _ALIASES.get(word, word)
        if word not in _NOISE and not word.isdigit():
            out.add(word)
    return frozenset(out)


def _same_word(a: str, b: str) -> bool:
    if a == b:
        return True
    return (
        min(len(a), len(b)) >= 4 and SequenceMatcher(None, a, b).ratio() >= _FUZZY_RATIO
    )


def _shared(label: frozenset[str], name: frozenset[str]) -> set[str]:
    """Words of ``label`` that also appear in ``name``, allowing small spelling gaps."""
    return {w for w in label if any(_same_word(w, n) for n in name)}


def _period(text: Any) -> str | None:
    folded = _ascii(text)
    for tag, pattern in _PERIODS:
        if re.search(pattern, folded):
            return tag
    return None


def _outcomes(market: Mapping[str, Any]) -> list[str]:
    raw = market.get('outcomes')
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return []
    return [str(o) for o in raw or []]


def utc_time(value: Any) -> datetime | None:
    """Parse the timestamps Kalshi, Gamma and Kairos return into aware UTC datetimes.

    >>> utc_time('2026-10-10 23:00:00+00').isoformat()
    '2026-10-10T23:00:00+00:00'
    """
    if not value:
        return None
    text = str(value).strip().replace(' ', 'T', 1).replace('Z', '+00:00')
    if text.endswith('+00'):
        text += ':00'
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo else None


# ── Venue identifiers ────────────────────────────────────────────────────


@dataclass(frozen=True)
class _KalshiTicker:
    yes_code: str
    other_code: str | None
    day: date | None
    start: datetime | None  # UTC, only when the ticker carries a time


def _eastern(day: date, hhmm: str) -> datetime:
    local = datetime(
        day.year, day.month, day.day, int(hhmm[:2]), int(hhmm[2:]), tzinfo=_ET
    )
    return local.astimezone(timezone.utc)


def _kalshi_ticker(ticker: str) -> _KalshiTicker:
    """Team codes, game date and start time encoded in a Kalshi market ticker.

    >>> t = _kalshi_ticker('KXNHLGAME-26OCT10DALPIT-DAL')
    >>> (t.yes_code, t.other_code, str(t.day), t.start)
    ('dal', 'pit', '2026-10-10', None)
    >>> _kalshi_ticker('KXAHLGAME-26OCT091900MANGRA-GRA').start.isoformat()
    '2026-10-09T23:00:00+00:00'
    """
    parts = ticker.split('-')
    if len(parts) < 3:
        return _KalshiTicker('', None, None, None)
    yes = parts[-1].lower()
    match = _KALSHI_EVENT.match(parts[1])
    if not match or match.group('mon') not in _MONTHS:
        return _KalshiTicker(yes, None, None, None)
    try:
        day = date(
            2000 + int(match.group('yy')),
            _MONTHS[match.group('mon')],
            int(match.group('dd')),
        )
    except ValueError:
        return _KalshiTicker(yes, None, None, None)
    hhmm = match.group('hhmm')
    start = (
        _eastern(day, hhmm)
        if hhmm and int(hhmm[:2]) < 24 and int(hhmm[2:]) < 60
        else None
    )
    codes = match.group('codes').lower()
    other = None
    if codes.endswith(yes) and len(codes) > len(yes):
        other = codes[: -len(yes)]
    elif codes.startswith(yes) and len(codes) > len(yes):
        other = codes[len(yes) :]
    return _KalshiTicker(yes, other, day, start)


@dataclass(frozen=True)
class _Slug:
    teams: list[str]
    suffix: list[str]
    day: date | None


def _slug(slug: str) -> _Slug:
    """Team codes, game date and trailing segments of a Polymarket market slug.

    >>> s = _slug('mls-clt-dal-2026-10-10-dal')
    >>> (s.teams, s.suffix, str(s.day))
    (['clt', 'dal'], ['dal'], '2026-10-10')
    """
    parts = slug.lower().split('-')
    for i, part in enumerate(parts):
        if i >= 1 and re.fullmatch(r'\d{4}', part) and len(parts) >= i + 3:
            try:
                day = date(int(part), int(parts[i + 1]), int(parts[i + 2]))
            except ValueError:
                continue
            return _Slug(parts[1:i], parts[i + 3 :], day)
    return _Slug([], [], None)


def _code_matches(kalshi_code: str | None, slug_code: str) -> bool:
    if not kalshi_code or not slug_code:
        return False
    if kalshi_code == slug_code:
        return True
    shorter = min(len(kalshi_code), len(slug_code))
    return shorter >= 3 and (
        slug_code.startswith(kalshi_code) or kalshi_code.startswith(slug_code)
    )


def _line(market: Mapping[str, Any], text: str) -> float | None:
    for key in ('line', 'floor_strike'):
        value = market.get(key)
        if value not in (None, ''):
            try:
                return abs(float(value))
            except (TypeError, ValueError):
                pass
    found = _LINE.findall(text.split(':')[-1])
    return float(found[-1]) if found else None


# ── Checks ───────────────────────────────────────────────────────────────

_VENUE_NAMES = {
    'kalshi': 'Kalshi',
    'polymarket': 'Polymarket',
    'predictfun': 'Predict.fun',
    'hyperliquid': 'Hyperliquid',
}


def _venue(market: Mapping[str, Any]) -> str:
    """Display name of the venue a market dict comes from (Gamma objects carry none)."""
    return _VENUE_NAMES.get(str(market.get('venue') or 'polymarket'), 'Polymarket')


#: How long after its scheduled time an open Kalshi market counts as postponed.
POSTPONED_AFTER = timedelta(hours=12)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _postponed(kalshi: Mapping[str, Any], start: datetime) -> bool:
    """Kalshi's game was due long ago, its market is still open, and it covers ``start``.

    Rain-delayed tennis matches look like this: Kalshi keeps the original date
    in the ticker, the other venue lists the new start, and Kalshi's rules
    (``after a ball has been played``) keep the market open until it is played.
    """
    if str(kalshi.get('status') or '') not in ('active', 'open'):
        return False
    due = utc_time(
        kalshi.get('occurrence_datetime') or kalshi.get('expected_expiration_time')
    )
    last = utc_time(kalshi.get('latest_expiration_time') or kalshi.get('close_time'))
    if due is None or last is None:
        return False
    return due < _now() - POSTPONED_AFTER and due < start <= last


def _schedule(
    kalshi: Mapping[str, Any],
    ticker: _KalshiTicker,
    slug: _Slug,
    poly: Mapping[str, Any],
) -> tuple[str, list[str]]:
    """A reason the two markets are about different games, and any warnings.

    A one-day gap is normal: Polymarket dates some games in UTC. It is accepted
    when Kalshi's expected expiration falls a few hours after Polymarket's start,
    or when Kalshi's rules do not tie the market to a date (tennis matches, for
    example, keep their market when postponed); the second case is flagged.
    """
    start = utc_time(poly.get('gameStartTime'))
    venue = _venue(poly)
    if start is None:
        # Venues without an exact start time (Predict.fun, Hyperliquid) only
        # publish a date; compare days, with one day of slack for time zones.
        day = utc_time(poly.get('eventDate'))
        other = day.astimezone(_ET).date() if day else slug.day
        if ticker.day and other and abs((other - ticker.day).days) > 1:
            if day and _postponed(kalshi, day):
                return '', [
                    f'postponed: Kalshi scheduled it for {ticker.day} and is still '
                    f'open; {venue} lists {other}'
                ]
            return f'different dates: Kalshi {ticker.day}, {venue} {other}', []
        return '', []
    if ticker.start is not None and abs(ticker.start - start) > START_TOLERANCE:
        return (
            f'different start times: Kalshi {ticker.start:%Y-%m-%d %H:%M}Z, '
            f'{venue} {start:%Y-%m-%d %H:%M}Z'
        ), []
    warnings = []
    if ticker.start is None and ticker.day is not None:
        gap = (start.astimezone(_ET).date() - ticker.day).days
        expires = utc_time(
            kalshi.get('occurrence_datetime') or kalshi.get('expected_expiration_time')
        )
        hours = (expires - start).total_seconds() / 3600.0 if expires else None
        low, high = EXPIRY_WINDOW
        consistent = hours is not None and low <= hours <= high
        dated = 'scheduled for' in str(kalshi.get('rules_primary') or '').lower()
        if abs(gap) > 1 or (gap and not consistent and dated):
            if not dated and _postponed(kalshi, start):
                return '', [
                    f'postponed: Kalshi scheduled it for {ticker.day} and is still '
                    f'open; {venue} starts {start:%Y-%m-%d %H:%M}Z'
                ]
            return (
                f'different dates: Kalshi {ticker.day}, '
                f'{venue} starts {start:%Y-%m-%d %H:%M}Z'
            ), []
        if gap and not consistent:
            warnings.append(
                f'Kalshi lists {ticker.day}, {venue} starts {start:%Y-%m-%d %H:%M}Z '
                '(postponed?)'
            )
    if slug.day and abs((start.date() - slug.day).days) > 1:
        warnings.append(f'rescheduled: {venue} first listed this game for {slug.day}')
    return '', warnings


#: Words that appear in many team names and cannot tell two teams apart alone.
GENERIC_WORDS = frozenset({'state', 'university', 'college'})


def _pick(
    label: frozenset[str],
    names: Sequence[frozenset[str]],
    ticker: _KalshiTicker,
    codes: Sequence[str],
) -> tuple[int | None, list[str], str]:
    """Pick one of two outcomes by name and by team code; refuse on any disagreement."""
    by_name = [
        i
        for i in (0, 1)
        if (_shared(label, names[i]) - _shared(label, names[1 - i])) - GENERIC_WORDS
    ]
    if not by_name:
        by_name = [i for i in (0, 1) if label and label == names[i] != names[1 - i]]
    name_pick = by_name[0] if len(by_name) == 1 else None
    code_pick = None
    if len(codes) == 2:
        yes_hits = [i for i in (0, 1) if _code_matches(ticker.yes_code, codes[i])]
        other_hits = [i for i in (0, 1) if _code_matches(ticker.other_code, codes[i])]
        if len(yes_hits) == 1 and other_hits in ([], [1 - yes_hits[0]]):
            code_pick = yes_hits[0]
        elif not yes_hits and len(other_hits) == 1:
            code_pick = 1 - other_hits[0]
    if name_pick is not None and code_pick is not None and name_pick != code_pick:
        return None, [], 'name and team-code checks point to different outcomes'
    evidence = (['name'] if name_pick is not None else []) + (
        ['team code'] if code_pick is not None else []
    )
    pick = name_pick if name_pick is not None else code_pick
    if pick is None:
        return None, [], 'could not tell which outcome is the Kalshi YES outcome'
    return pick, evidence, ''


def _align_totals(
    kalshi: Mapping[str, Any], poly: Mapping[str, Any], outcomes: list[str], label: str
) -> OutcomeAlignment:
    k_line = _line(kalshi, label)
    p_line = _line(poly, str(poly.get('question') or ''))
    if k_line is None or p_line is None:
        return OutcomeAlignment.reject('totals', 'no line to compare')
    if abs(k_line - p_line) > 1e-9:
        return OutcomeAlignment.reject(
            'totals', f'different lines: Kalshi {k_line:g}, {_venue(poly)} {p_line:g}'
        )
    strike = str(kalshi.get('strike_type') or '')
    k_side = (
        'under'
        if strike.startswith('less') or _ascii(label).startswith('under')
        else 'over'
    )
    lowered = [o.strip().lower() for o in outcomes]
    if k_side not in lowered:
        return OutcomeAlignment.reject('totals', f'no {k_side.title()} outcome')
    return OutcomeAlignment.at(
        'totals', lowered.index(k_side), [f'line {k_line:g} on both venues', k_side]
    )


def _align_binary(
    label: str,
    ticker: _KalshiTicker,
    slug: _Slug,
    poly: Mapping[str, Any],
) -> OutcomeAlignment:
    """The Polymarket market asks about one outcome; it must be the Kalshi one."""
    question = str(poly.get('question') or '')
    group = str(poly.get('groupItemTitle') or '')
    k_tie = ticker.yes_code in ('tie', 'draw') or bool(
        {'tie', 'draw'} & set(_ascii(label).split())
    )
    p_tie = 'draw' in _ascii(f'{question} {group}') or slug.suffix[-1:] == ['draw']
    if k_tie or p_tie:
        if k_tie and p_tie:
            return OutcomeAlignment.at('binary', 0, ['draw on both venues'])
        return OutcomeAlignment.reject('binary', 'only one side is the draw outcome')
    if not poly.get('sportsMarketType'):
        return OutcomeAlignment.reject(
            'binary', 'not a sports market; questions not compared'
        )

    label_words = words(label)
    subject = words(group) if group else words(question)
    titles = ' '.join(
        str(e.get('title', ''))
        for e in poly.get('events') or []
        if isinstance(e, Mapping)
    )
    opponent = frozenset(words(titles) - subject)
    suffix = slug.suffix[0] if slug.suffix else ''
    by_name = _shared(label_words, subject) - _shared(label_words, opponent)
    against_name = _shared(label_words, opponent) - _shared(label_words, subject)
    by_code = _code_matches(ticker.yes_code, suffix)
    against_code = not by_code and _code_matches(ticker.other_code, suffix)
    if against_name or against_code:
        return OutcomeAlignment.reject(
            'binary', f'{_venue(poly)} asks about the other team'
        )
    evidence = (['name'] if by_name else []) + (['team code'] if by_code else [])
    if not evidence:
        return OutcomeAlignment.reject(
            'binary', 'could not confirm both markets ask about the same team'
        )
    return OutcomeAlignment.at('binary', 0, evidence)


def align_kalshi_polymarket(
    kalshi: Mapping[str, Any], polymarket: Mapping[str, Any]
) -> OutcomeAlignment:
    """Find the Polymarket outcome that pays exactly when the Kalshi market's YES pays.

    Args:
        kalshi: A Kalshi market object (``GET /markets``).
        polymarket: A Polymarket Gamma market object (``GET /markets``).
    """
    outcomes = _outcomes(polymarket)
    lowered = [o.strip().lower() for o in outcomes]
    question = str(polymarket.get('question') or '')
    market_type = str(polymarket.get('sportsMarketType') or '').lower()
    if len(outcomes) != 2:
        return OutcomeAlignment.reject('other', f'{len(outcomes)} outcomes')
    if lowered == ['yes', 'no']:
        kind = 'binary'
    elif lowered == ['over', 'under'] or market_type == 'totals':
        kind = 'totals'
    elif market_type.startswith('spread') or 'spread' in question.lower():
        kind = 'spreads'
    else:
        kind = 'head_to_head'

    label = str(kalshi.get('yes_sub_title') or '').strip() or re.sub(
        r'\s+wins?\??$', '', str(kalshi.get('title') or '').strip()
    )
    k_text = f"{kalshi.get('title', '')} {label}"
    p_text = f"{question} {polymarket.get('groupItemTitle', '')} {polymarket.get('slug', '')}"
    if _period(k_text) != _period(p_text.replace('-', ' ')):
        return OutcomeAlignment.reject(
            kind, 'different periods (for example half vs full game)'
        )
    ticker = _kalshi_ticker(str(kalshi.get('ticker') or ''))
    slug = _slug(str(polymarket.get('slug') or ''))
    problem, warnings = _schedule(kalshi, ticker, slug, polymarket)
    if problem:
        return OutcomeAlignment.reject(kind, problem)

    if kind == 'totals':
        result = _align_totals(kalshi, polymarket, outcomes, label)
    elif kind == 'spreads':
        result = OutcomeAlignment.reject(
            'spreads', 'spread markets are not aligned yet'
        )
    elif kind == 'binary':
        result = _align_binary(label, ticker, slug, polymarket)
    else:
        index, evidence, problem = _pick(
            words(label), [words(o) for o in outcomes], ticker, slug.teams
        )
        if index is None:
            return OutcomeAlignment.reject(kind, problem)
        result = OutcomeAlignment.at(kind, index, evidence)
    return result.warn(warnings)


# ── Two-outcome markets on other venues ──────────────────────────────────


def _letters(text: Any) -> str:
    return re.sub(r'[^a-z]', '', _ascii(text))


def labels_agree(x: Any, y: Any) -> bool:
    """Whether two outcome labels clearly name the same thing.

    Words must overlap, or one label must start a word of the other
    (``NEMI1`` for Nemiga, ``AST10`` for Astralis). Looser abbreviations are
    left to :func:`abbreviates`, because short codes match too much.

    >>> labels_agree('Over 21.5', 'Over'), labels_agree('NEMI1', 'Nemiga')
    (True, True)
    >>> labels_agree('TEN', 'Texans'), labels_agree('Over', 'Under')
    (False, False)
    """
    wx, wy = (
        words(re.sub(r'[\d.]+', ' ', str(x))),
        words(re.sub(r'[\d.]+', ' ', str(y))),
    )
    if wx and wy and (wx <= wy or wy <= wx or _shared(wx, wy)):
        return True
    for short, long in ((x, y), (y, x)):
        code = _letters(short)
        if len(code) >= 3 and any(
            w.startswith(code) for w in re.findall(r'[a-z]+', _ascii(long))
        ):
            return True
    return False


def abbreviates(code: Any, name: Any) -> bool:
    """Whether ``code`` could abbreviate ``name``: same first letter, letters in order.

    >>> abbreviates('WVIR', 'West Virginia'), abbreviates('TTG', 'Talent Gaming')
    (True, True)
    >>> abbreviates('HOU', 'Texans')
    False
    """
    short, long = _letters(code), _letters(name)
    if len(short) < 2 or not long or short[0] != long[0]:
        return False
    rest = iter(long)
    return all(letter in rest for letter in short)


def _same_question(a: Mapping[str, Any], b: Mapping[str, Any]) -> bool:
    qa, qb = str(a.get('question') or ''), str(b.get('question') or '')
    squash = lambda text: re.sub(r'\W+', ' ', _ascii(text)).strip()  # noqa: E731
    return bool(qa) and squash(qa) == squash(qb)


def _event_slug(market: Mapping[str, Any]) -> str:
    if market.get('eventSlug'):
        return str(market['eventSlug'])
    events = market.get('events') or []
    first = events[0] if events and isinstance(events[0], Mapping) else {}
    return str(first.get('slug') or '')


def _event_day(market: Mapping[str, Any]) -> date | None:
    moment = utc_time(market.get('gameStartTime')) or utc_time(market.get('eventDate'))
    return moment.astimezone(_ET).date() if moment else None


def _same_event(
    a: Mapping[str, Any], b: Mapping[str, Any]
) -> tuple[str, list[str], list[str]]:
    """A reason the two markets are about different events, evidence and warnings."""
    slug_a, slug_b = _event_slug(a), _event_slug(b)
    if slug_a and slug_b:
        if slug_a == slug_b:
            return '', ['same event'], []
        ea, eb = _slug(slug_a), _slug(slug_b)
        if (
            ea.day
            and eb.day
            and (abs((ea.day - eb.day).days) > 1 or ea.teams != eb.teams)
        ):
            return f'different events: {slug_a} vs {slug_b}', [], []
    day_a, day_b = _event_day(a), _event_day(b)
    if day_a and day_b and abs((day_a - day_b).days) > 1:
        return f'different dates: {_venue(a)} {day_a}, {_venue(b)} {day_b}', [], []
    if slug_a and slug_b:
        return '', [], [f'event ids differ ({slug_a} vs {slug_b})']
    return '', [], []


def _subject(market: Mapping[str, Any]) -> frozenset[str]:
    group = str(market.get('groupItemTitle') or '')
    return words(group) if group else words(market.get('question'))


def _map_outcomes(first: Sequence[str], second: Sequence[str]) -> int | None:
    """Index in ``second`` of ``first[0]``, if the labels say so unambiguously."""
    direct = labels_agree(first[0], second[0]) + labels_agree(first[1], second[1])
    crossed = labels_agree(first[0], second[1]) + labels_agree(first[1], second[0])
    if direct > crossed:
        return 0
    if crossed > direct:
        return 1
    return None


def align_two_outcome_markets(
    first: Mapping[str, Any], second: Mapping[str, Any]
) -> OutcomeAlignment:
    """Find the outcome of ``second`` that pays exactly when ``first``'s first outcome pays.

    Both markets are in Polymarket's shape (a Gamma market object, or Kairos
    metadata converted by :func:`oracle3_extras.market.kairos.markets.as_polymarket_shape`).
    Predict.fun lists copies of Polymarket markets, so the commonest case is the
    same question in the same event; its outcomes line up in order unless the
    labels say they are reversed.
    """
    outcomes_a, outcomes_b = _outcomes(first), _outcomes(second)
    if len(outcomes_a) != 2 or len(outcomes_b) != 2:
        return OutcomeAlignment.reject('other', 'not two-outcome markets')
    lowered_a = [o.strip().lower() for o in outcomes_a]
    lowered_b = [o.strip().lower() for o in outcomes_b]
    text_a = f"{first.get('question', '')} {first.get('groupItemTitle', '')}"
    text_b = f"{second.get('question', '')} {second.get('groupItemTitle', '')}"
    kind = (
        'binary'
        if lowered_a == ['yes', 'no']
        else 'totals'
        if lowered_a[0].startswith('over')
        else 'head_to_head'
    )
    if _period(text_a) != _period(text_b):
        return OutcomeAlignment.reject(
            kind, 'different periods (for example half vs full game)'
        )
    problem, evidence, warnings = _same_event(first, second)
    if problem:
        return OutcomeAlignment.reject(kind, problem)

    if _same_question(first, second):
        index = _map_outcomes(outcomes_a, outcomes_b)
        index = 0 if index is None else index
        labels = ['same outcomes'] if lowered_a == lowered_b else []
        reversed_note = ['outcomes listed in reverse'] if index else []
        return OutcomeAlignment.at(
            kind, index, ['same question', *evidence, *labels, *reversed_note]
        ).warn(warnings)

    binary_a, binary_b = lowered_a == ['yes', 'no'], lowered_b == ['yes', 'no']
    if kind == 'totals' or lowered_b[0].startswith('over'):
        line_a = _line(first, str(first.get('question') or ''))
        line_b = _line(second, str(second.get('question') or ''))
        if line_a is None or line_b is None or abs(line_a - line_b) > 1e-9:
            return OutcomeAlignment.reject('totals', 'different or missing lines')
        index = _map_outcomes(outcomes_a, outcomes_b)
    elif binary_a and binary_b:
        tie_a, tie_b = ('draw' in _ascii(text_a)), ('draw' in _ascii(text_b))
        if tie_a or tie_b:
            index = 0 if tie_a and tie_b else None
        else:
            sa, sb = _subject(first), _subject(second)
            index = 0 if sa and sb and (_shared(sa, sb) or _shared(sb, sa)) else None
    elif binary_a:
        subject = _subject(first)
        hits = [
            i
            for i in (0, 1)
            if _shared(subject, words(outcomes_b[i]))
            or labels_agree(first.get('groupItemTitle') or '', outcomes_b[i])
        ]
        index = hits[0] if len(hits) == 1 else None
    elif binary_b:
        subject = _subject(second)
        hits = [
            i
            for i in (0, 1)
            if _shared(subject, words(outcomes_a[i]))
            or labels_agree(second.get('groupItemTitle') or '', outcomes_a[i])
        ]
        # second's YES is first's outcome hits[0]; first's outcome 0 pays with YES (0) or NO (1)
        index = (0 if hits[0] == 0 else 1) if len(hits) == 1 else None
    else:
        index = _map_outcomes(outcomes_a, outcomes_b)
    if index is None:
        return OutcomeAlignment.reject(kind, 'could not line up the outcomes')
    return OutcomeAlignment.at(kind, index, [*evidence, 'outcome names']).warn(warnings)
