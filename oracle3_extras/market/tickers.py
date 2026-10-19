"""Prediction markets that price public companies, matched to stock tickers.

Polymarket lists company markets with the ticker in the title ("NVIDIA (NVDA)
Up or Down", "Will Equifax (EFX) beat quarterly earnings?") under its finance
tags; Kalshi names the company instead ("Starbucks total global stores in
2026", "What will Google say during their next earnings call?").
:func:`ticker_markets` reads both venues' open events, matches them to
tickers, labels each with an event type, and adds the most traded market's
price, so a list of tickers (a sector, a watchlist, the S&P 500) can be run
against every open market at once.

Matching is conservative. A ticker in parentheses counts only in a Polymarket
finance event (confidence 1.0); otherwise a company name must appear as a whole
word in the event title (0.7), and names that are also everyday words, places,
people or teams ("Target", "Delta", "Visa") are never matched alone. Markets
that price a company without naming it, such as GPU rental prices for
NVIDIA's chips on Polymarket, are out of reach; Dune's ticker mapping (an
Enterprise dataset) covers those, and would return the same :class:`TickerEvent`
rows.

The default company list is the S&P 500 (``data/sp500.csv``, from the
datasets/s-and-p-500-companies project under ODC-PDDL, updated 15 October
2026), with the names people use for each company.
"""

from __future__ import annotations

import csv
import json
import logging
import re
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from importlib import resources
from typing import Any

from oracle3_extras._http import gather_limited, new_client, request_json
from oracle3_extras.venues import GAMMA_API, KALSHI_API, KALSHI_CONCURRENCY

__all__ = [
    'EVENT_TYPES',
    'Company',
    'TickerEvent',
    'TickerMarkets',
    'event_type',
    'match_kalshi',
    'match_polymarket',
    'sp500',
    'ticker_markets',
]

logger = logging.getLogger(__name__)

#: Polymarket tags whose open events are searched. ``finance`` carries every
#: market with a ticker in its title; the others add markets that only name
#: the company (earnings-call mentions, headcount, outages).
POLYMARKET_TAGS = (
    'finance',
    'equities',
    'stocks',
    'earnings',
    'kpis',
    'tech',
    'big-tech',
    'business',
    'earnings-calls',
)
#: A parenthesised ticker counts only in events with one of these tags; in
#: other events "(FL)" is Florida and "(OH)" Ohio.
FINANCE_TAGS = frozenset({'finance', 'equities', 'stocks', 'earnings', 'kpis'})
#: Codes Polymarket also writes in parentheses that are indices, rates,
#: commodities, currencies or financial metrics rather than stocks.
NOT_TICKERS = frozenset(
    {
        'SPX', 'NDX', 'DJIA', 'DJI', 'RUT', 'VIX', 'DXY', 'DAX', 'FTSE', 'NKY',
        'NIK', 'NYA', 'UKX', 'HSI', 'CAC', 'WTI', 'NG', 'GC', 'CL', 'SI', 'HG',
        'BTC', 'ETH', 'SOL', 'XRP', 'USD', 'EUR', 'JPY', 'GBP', 'CNY', 'CPI',
        'GDP', 'PCE', 'FOMC', 'FED', 'ECB', 'BOJ', 'IPO', 'CEO', 'AI', 'US',
        'UK', 'EU', 'ET', 'PT', 'UTC', 'EST', 'EDT', 'Q1', 'Q2', 'Q3', 'Q4',
        'AUM', 'CC', 'MBR', 'NOI', 'SSNOI', 'RPO', 'ARR', 'NRR', 'GMV', 'ARPU',
        'TPV', 'EBITDA', 'FCF', 'GAAP', 'YOY', 'DAU', 'MAU',
    }
)  # fmt: skip
#: Titles that use a company's platform as a yardstick ("most searched on
#: Google", "#1 app in the App Store") without being about the company.
_NOT_ABOUT_THE_COMPANY = re.compile(
    r'\bsearched\b|year in search|\bapp store\b|netflix (show|movie)', re.I
)
#: Event types, checked in this order against the event title.
EVENT_TYPES = (
    'mentions',
    'price',
    'earnings',
    'kpi',
    'legal',
    'deal',
    'leadership',
    'product',
    'other',
)
_TYPE_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ('mentions', re.compile(r'\b(say|said|mention)', re.I)),
    (
        'price',
        re.compile(
            r'up or down|close[sd]? (above|below|at|week)|finish (the )?week|'
            r'\bhit\b|all[- ]time high|market cap|largest company|stock price|'
            r'share price|valuation',
            re.I,
        ),
    ),
    ('earnings', re.compile(r'earnings|\bEPS\b|guidance|quarterly results', re.I)),
    (
        'kpi',
        # Reported operating numbers, and the alternative data that tracks
        # them (foot traffic, card spend, app downloads, menu prices).
        re.compile(
            r'revenue|growth|sales|stores|deliveries|production|users|customers|'
            r'subscribers|\bsubs\b|headcount|employees|shipments|shipped|accounts|'
            r'margin|volume|restaurants|procedures|bookings|booked|backlog|units|'
            r'cash flow|capex|capital expenditure|clicks|impression|daily active|'
            r'monthly active|\bDAU\b|\bMAU\b|\bARR\b|market share|share of requests|'
            r'compute|average hourly price|passengers|seats flown|load factor|'
            r'\bfare\b|\btrips\b|\brides\b|\borders\b|rooms|RevPAR|cards|cardholders|'
            r'customer count|transactions|net additions|deployment|gigawatt|'
            r'berth|operating ratio|combined ratio|benefit ratio|assets under|'
            r'exabytes|foot traffic|card spend|downloads|\bviews\b|price in \w+$|'
            r'\bcount\b|\bproduce|\bgrow\b',
            re.I,
        ),
    ),
    (
        'legal',
        re.compile(
            r'court|antitrust|lawsuit|\bsue\b|\bcase\b|ordered to pay|\bFTC\b|'
            r'\bDOJ\b|\bSEC\b|\bFDA\b|monopoly|ruling|trial|settle|\bfine[ds]?\b|'
            r'\bban\b|investigat|tariff|regulat|government',
            re.I,
        ),
    ),
    (
        'deal',
        re.compile(
            r'acquir|merger|merge|take ?over|buyout|\bbuy\b|\bsell\b|purchase|stake|'
            r'\bIPO\b|listing|go public|spin[- ]?off|\bdeal\b|licens|agree to|'
            r'contract',
            re.I,
        ),
    ),
    (
        'leadership',
        re.compile(
            r'\bCEO\b|\bCOO\b|\bCFO\b|chair|step down|resign|fired|replace|'
            r'successor|\bleaves?\b|rejoin|\bback\b',
            re.I,
        ),
    ),
    (
        'product',
        re.compile(
            r'launch|release|unveil|announce|reveal|outage|disrupt|incident|'
            r'deliver|open orders|stop selling|price increase|raises .*price|'
            r'token price|cost\b|arena|humanity|\bmodel\b',
            re.I,
        ),
    ),
)

_TICKER_IN_TITLE = re.compile(r'\(([A-Z]{1,5}(?:\.[A-Z])?)\)')
_LEADING = re.compile(r'^(?:what|which|how|will|when|does|is|the)\b\s*', re.I)
_BOUNDARY_BEFORE = r'(?<![A-Za-z0-9-])'
_BOUNDARY_AFTER = r'(?![A-Za-z0-9-])'

NAME_CONFIDENCE = 0.7
TICKER_CONFIDENCE = 1.0


@dataclass(frozen=True)
class Company:
    """A public company and the names that identify it in market titles."""

    ticker: str
    name: str
    names: tuple[str, ...] = ()
    sector: str = ''


def sp500() -> list[Company]:
    """The bundled S&P 500 list (15 October 2026), with the names to match.

    Companies whose every name is ambiguous (Visa, Chevron, Target's short
    name) have no names and are matched only by a ticker in the title.
    """
    text = (
        resources.files('oracle3_extras.market')
        .joinpath('data/sp500.csv')
        .read_text(encoding='utf-8')
    )
    return [
        Company(
            ticker=row['ticker'],
            name=row['company'],
            names=tuple(n for n in row['names'].split(';') if n),
            sector=row['sector'],
        )
        for row in csv.DictReader(text.splitlines())
    ]


def event_type(title: str) -> str:
    """What kind of company event a market title is about (see :data:`EVENT_TYPES`)."""
    for kind, pattern in _TYPE_RULES:
        if pattern.search(title):
            return kind
    return 'other'


@dataclass
class TickerEvent:
    """One open event that prices a company, on one venue.

    ``volume_24h_usd`` is Polymarket's dollar volume; Kalshi reports contracts
    traded, in ``volume_24h_contracts``. ``top_market`` is the event's most
    traded market and ``yes_price`` its last trade (or midpoint), 0 to 1.
    """

    ticker: str
    company: str
    venue: str
    event_id: str
    title: str
    event_type: str
    evidence: str
    confidence: float
    markets: int = 0
    closes: str | None = None
    url: str | None = None
    top_market: str | None = None
    top_market_id: str | None = None
    yes_price: float | None = None
    volume_24h_usd: float | None = None
    volume_24h_contracts: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TickerMarkets:
    """Every matched event, and what was searched."""

    events: list[TickerEvent] = field(default_factory=list)
    searched: dict[str, int] = field(default_factory=dict)

    def for_ticker(self, ticker: str) -> list[TickerEvent]:
        return [e for e in self.events if e.ticker == ticker.upper()]

    def summary(self, top: int = 15) -> dict[str, Any]:
        tickers = Counter(e.ticker for e in self.events)
        return {
            'events_searched': dict(self.searched),
            'events': len(self.events),
            'companies': len(tickers),
            'by_venue': dict(Counter(e.venue for e in self.events).most_common()),
            'by_event_type': dict(
                Counter(e.event_type for e in self.events).most_common()
            ),
            'by_evidence': dict(Counter(e.evidence for e in self.events).most_common()),
            'top_tickers': dict(tickers.most_common(top)),
        }


# ── Matching ─────────────────────────────────────────────────────────────


class _Names:
    """One regex over every company name; longest names win at a position."""

    def __init__(self, companies: Iterable[Company]) -> None:
        self.by_name: dict[str, list[Company]] = {}
        for company in companies:
            for name in company.names:
                self.by_name.setdefault(name, []).append(company)
        names = sorted(self.by_name, key=len, reverse=True)
        self.pattern = (
            re.compile(
                _BOUNDARY_BEFORE
                + '('
                + '|'.join(re.escape(n) for n in names)
                + ')'
                + _BOUNDARY_AFTER
            )
            if names
            else None
        )

    def find(self, text: str) -> list[Company]:
        if self.pattern is None or not text:
            return []
        found: dict[str, Company] = {}
        for match in self.pattern.finditer(text):
            for company in self.by_name[match.group(1)]:
                found.setdefault(company.ticker, company)
        return list(found.values())


def _name_from_title(title: str, ticker: str) -> str:
    """The words before ``(TICKER)``: "Will Rocket Lab (RKLB) ..." gives "Rocket Lab"."""
    before = title.split(f'({ticker})', 1)[0]
    before = re.split(r'[?:|]', before)[-1].strip()
    while True:
        stripped = _LEADING.sub('', before)
        if stripped == before:
            break
        before = stripped
    return before.strip(' ,') or ticker


def _tags(event: Mapping[str, Any]) -> set[str]:
    return {str(t.get('slug') or '') for t in event.get('tags') or []}


def _float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number


def _mid(bid: Any, ask: Any) -> float | None:
    """Midpoint of a two-sided book; ``None`` when one side is empty (bid 0 or ask 1)."""
    low, high = _float(bid), _float(ask)
    if low is None or high is None or not 0 < low < high < 1:
        return None
    return round((low + high) / 2, 4)


def _polymarket_prices(event: Mapping[str, Any]) -> dict[str, Any]:
    markets = [m for m in event.get('markets') or [] if not m.get('closed')]
    out: dict[str, Any] = {
        'markets': len(markets),
        'volume_24h_usd': _float(event.get('volume24hr')),
    }
    if not markets:
        return out
    top = max(markets, key=lambda m: _float(m.get('volume24hr')) or 0.0)
    price = _float(top.get('lastTradePrice')) or _mid(
        top.get('bestBid'), top.get('bestAsk')
    )
    if not price:
        try:
            price = _float(json.loads(top.get('outcomePrices') or '[]')[0])
        except (ValueError, IndexError, TypeError):
            price = None
    out.update(
        top_market=top.get('groupItemTitle') or top.get('question'),
        top_market_id=str(top.get('id') or '') or None,
        yes_price=price,
    )
    return out


def match_polymarket(
    events: Iterable[Mapping[str, Any]],
    companies: Sequence[Company],
    *,
    only: set[str] | None = None,
) -> list[TickerEvent]:
    """Match open Polymarket events (Gamma ``/events`` rows) to tickers.

    Args:
        companies: Names to look for; a ticker in a finance event's title also
            counts for companies not on the list, unless ``only`` is given.
        only: Keep these tickers only.
    """
    names = _Names(companies)
    known = {c.ticker: c for c in companies}
    found: list[TickerEvent] = []
    for event in events:
        title = str(event.get('title') or '')
        slug = str(event.get('slug') or event.get('id') or '')
        hits: dict[str, tuple[str, str, float]] = {}
        if _tags(event) & FINANCE_TAGS:
            # The first parenthesised code follows the company's name; later
            # ones are usually metrics ("(AUM)", "(CC)") unless already known.
            for n, ticker in enumerate(_TICKER_IN_TITLE.findall(title)):
                if ticker in NOT_TICKERS or (n and ticker not in known):
                    continue
                company = known.get(ticker)
                name = company.name if company else _name_from_title(title, ticker)
                hits.setdefault(ticker, (name, 'ticker in title', TICKER_CONFIDENCE))
        for company in (
            [] if _NOT_ABOUT_THE_COMPANY.search(title) else names.find(title)
        ):
            hits.setdefault(
                company.ticker, (company.name, 'company name', NAME_CONFIDENCE)
            )
        for ticker, (name, evidence, confidence) in hits.items():
            if only is not None and ticker not in only:
                continue
            found.append(
                TickerEvent(
                    ticker=ticker,
                    company=name,
                    venue='polymarket',
                    event_id=slug,
                    title=title,
                    event_type=event_type(title),
                    evidence=evidence,
                    confidence=confidence,
                    closes=event.get('endDate'),
                    url=f'https://polymarket.com/event/{slug}' if slug else None,
                    **_polymarket_prices(event),
                )
            )
    return found


def match_kalshi(
    events: Iterable[Mapping[str, Any]],
    companies: Sequence[Company],
    *,
    only: set[str] | None = None,
) -> list[TickerEvent]:
    """Match open Kalshi events (``/events`` rows) to tickers by company name.

    Sports events are skipped: team names collide with company names.
    """
    names = _Names(companies)
    found: list[TickerEvent] = []
    for event in events:
        if event.get('category') == 'Sports':
            continue
        title = str(event.get('title') or '')
        text = f"{title} {event.get('sub_title') or ''}".strip()
        if _NOT_ABOUT_THE_COMPANY.search(text):
            continue
        for company in names.find(text):
            if only is not None and company.ticker not in only:
                continue
            kind = (
                'mentions' if event.get('category') == 'Mentions' else event_type(title)
            )
            found.append(
                TickerEvent(
                    ticker=company.ticker,
                    company=company.name,
                    venue='kalshi',
                    event_id=str(event.get('event_ticker') or ''),
                    title=title,
                    event_type=kind,
                    evidence='company name',
                    confidence=NAME_CONFIDENCE,
                )
            )
    return found


def _kalshi_prices(event: TickerEvent, markets: Sequence[Mapping[str, Any]]) -> None:
    live = [m for m in markets if m.get('status') in (None, 'active', 'open')]
    event.markets = len(live)
    if not live:
        return
    volume = lambda m: _float(m.get('volume_24h_fp') or m.get('volume_24h')) or 0.0  # noqa: E731
    top = max(live, key=volume)
    price = _float(top.get('last_price_dollars')) or _mid(
        top.get('yes_bid_dollars'), top.get('yes_ask_dollars')
    )
    event.top_market = top.get('yes_sub_title') or top.get('title')
    event.top_market_id = top.get('ticker')
    event.yes_price = price
    event.volume_24h_contracts = round(sum(volume(m) for m in live), 2)
    event.closes = min(
        (str(m['close_time']) for m in live if m.get('close_time')), default=None
    )


# ── Fetching ─────────────────────────────────────────────────────────────

#: Gamma refuses offsets beyond this; one tag has far fewer open events.
GAMMA_MAX_OFFSET = 2000


async def polymarket_events(
    tags: Sequence[str] = POLYMARKET_TAGS,
) -> list[dict[str, Any]]:
    """Open Polymarket events under any of ``tags``, each once."""
    events: dict[str, dict[str, Any]] = {}
    async with new_client() as client:
        for tag in tags:
            offset = 0
            while offset <= GAMMA_MAX_OFFSET:
                page = await request_json(
                    client,
                    'GET',
                    f'{GAMMA_API}/events',
                    params={
                        'closed': 'false',
                        'tag_slug': tag,
                        'limit': 100,
                        'offset': offset,
                    },
                )
                for event in page or []:
                    events.setdefault(str(event.get('id')), event)
                if len(page or []) < 100:
                    break
                offset += 100
    return list(events.values())


async def kalshi_events() -> list[dict[str, Any]]:
    """Every open Kalshi event (without its markets)."""
    events: list[dict[str, Any]] = []
    cursor: str | None = None
    async with new_client() as client:
        while True:
            params: dict[str, Any] = {'status': 'open', 'limit': 200}
            if cursor:
                params['cursor'] = cursor
            data = await request_json(
                client, 'GET', f'{KALSHI_API}/events', params=params
            )
            events.extend(data.get('events') or [])
            cursor = data.get('cursor') or None
            if not cursor:
                return events


async def kalshi_event_markets(
    event_tickers: Iterable[str],
) -> dict[str, list[dict[str, Any]]]:
    """Each event's markets; Kalshi takes one event per request."""
    tickers = list(dict.fromkeys(t for t in event_tickers if t))
    found: dict[str, list[dict[str, Any]]] = {}
    async with new_client() as client:

        async def one(event_ticker: str) -> None:
            data = await request_json(
                client,
                'GET',
                f'{KALSHI_API}/markets',
                params={'event_ticker': event_ticker, 'limit': 1000},
            )
            found[event_ticker] = data.get('markets') or []

        await gather_limited([one(t) for t in tickers], limit=KALSHI_CONCURRENCY)
    return found


async def ticker_markets(
    companies: Iterable[Company] | None = None,
    *,
    venues: Sequence[str] = ('kalshi', 'polymarket'),
    types: Iterable[str] | None = None,
    min_confidence: float = 0.0,
    prices: bool = True,
) -> TickerMarkets:
    """Open Kalshi and Polymarket events that price the given companies.

    Args:
        companies: Companies to look for; by default the S&P 500, plus any
            ticker Polymarket writes in a finance event's title. When given,
            only these tickers are kept.
        venues: ``kalshi``, ``polymarket`` or both.
        types: Keep these event types only (see :data:`EVENT_TYPES`).
        min_confidence: 1.0 keeps only tickers written in the title.
        prices: Add each event's most traded market and its price (one extra
            request per Kalshi event).
    """
    given = list(companies) if companies is not None else None
    universe = given if given is not None else sp500()
    only = {c.ticker for c in given} if given is not None else None
    wanted = set(types) if types is not None else None
    result = TickerMarkets()
    if 'polymarket' in venues:
        events = await polymarket_events()
        result.searched['polymarket'] = len(events)
        result.events += match_polymarket(events, universe, only=only)
    if 'kalshi' in venues:
        events = await kalshi_events()
        result.searched['kalshi'] = len(events)
        result.events += match_kalshi(events, universe, only=only)
    result.events = [
        e
        for e in result.events
        if e.confidence >= min_confidence and (wanted is None or e.event_type in wanted)
    ]
    kalshi = [e for e in result.events if e.venue == 'kalshi']
    if prices and kalshi:
        markets = await kalshi_event_markets(e.event_id for e in kalshi)
        for event in kalshi:
            _kalshi_prices(event, markets.get(event.event_id, []))
    result.events.sort(
        key=lambda e: (
            e.ticker,
            -(e.volume_24h_usd or e.volume_24h_contracts or 0.0),
            e.venue,
        )
    )
    logger.info(
        'Tickers: %d events for %d companies',
        len(result.events),
        len({e.ticker for e in result.events}),
    )
    return result
