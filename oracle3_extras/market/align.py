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

__all__ = ['OutcomeAlignment', 'align_kalshi_polymarket', 'words']

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


def _utc(value: Any) -> datetime | None:
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
    start = _utc(poly.get('gameStartTime'))
    if start is None:
        if ticker.day and slug.day and abs((slug.day - ticker.day).days) > 1:
            return f'different dates: Kalshi {ticker.day}, Polymarket {slug.day}', []
        return '', []
    if ticker.start is not None and abs(ticker.start - start) > START_TOLERANCE:
        return (
            f'different start times: Kalshi {ticker.start:%Y-%m-%d %H:%M}Z, '
            f'Polymarket {start:%Y-%m-%d %H:%M}Z'
        ), []
    warnings = []
    if ticker.start is None and ticker.day is not None:
        gap = (start.astimezone(_ET).date() - ticker.day).days
        expires = _utc(
            kalshi.get('occurrence_datetime') or kalshi.get('expected_expiration_time')
        )
        hours = (expires - start).total_seconds() / 3600.0 if expires else None
        low, high = EXPIRY_WINDOW
        consistent = hours is not None and low <= hours <= high
        dated = 'scheduled for' in str(kalshi.get('rules_primary') or '').lower()
        if abs(gap) > 1 or (gap and not consistent and dated):
            return (
                f'different dates: Kalshi {ticker.day}, '
                f'Polymarket starts {start:%Y-%m-%d %H:%M}Z'
            ), []
        if gap and not consistent:
            warnings.append(
                f'Kalshi lists {ticker.day}, Polymarket starts {start:%Y-%m-%d %H:%M}Z '
                '(postponed?)'
            )
    if slug.day and abs((start.date() - slug.day).days) > 1:
        warnings.append(
            f'rescheduled: Polymarket first listed this game for {slug.day}'
        )
    return '', warnings


def _pick(
    label: frozenset[str],
    names: Sequence[frozenset[str]],
    ticker: _KalshiTicker,
    codes: Sequence[str],
) -> tuple[int | None, list[str], str]:
    """Pick one of two outcomes by name and by team code; refuse on any disagreement."""
    by_name = [
        i for i in (0, 1) if _shared(label, names[i]) - _shared(label, names[1 - i])
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
            'totals', f'different lines: Kalshi {k_line:g}, Polymarket {p_line:g}'
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
        return OutcomeAlignment.reject('binary', 'Polymarket asks about the other team')
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
