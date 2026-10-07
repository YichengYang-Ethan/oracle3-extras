"""Kairos market metadata in the shape the outcome aligner reads.

Predict.fun and Hyperliquid markets are looked up through Kairos's public
Market Data API (``POST /v1/markets/batch``), which returns each market's
title, outcomes with token ids, status, fees and settlement state. Predict.fun
lists copies of Polymarket markets, and Hyperliquid's HIP-4 markets have the
same two-outcome shape, so :func:`as_polymarket_shape` converts that metadata
into the fields of a Polymarket Gamma market that
:mod:`oracle3_extras.market.align` reads.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from oracle3_extras.market.align import abbreviates, labels_agree, words

__all__ = ['OPEN_STATUSES', 'as_polymarket_shape', 'expand_labels']

OPEN_STATUSES = frozenset({'', 'open', 'active'})

_SEPARATORS = re.compile(r'\s+(?:v|vs\.?|@|at)\s+|:', re.IGNORECASE)


def _segments(question: str) -> list[str]:
    return [s.strip() for s in _SEPARATORS.split(question) if s.strip()]


def _teams(question: str) -> list[str]:
    """The two sides of the last ``A vs B`` clause of a question, if there is one."""
    clause = question.split(':')[-1] if ' v' in question.split(':')[-1] else question
    for part in reversed(question.split(':')):
        if re.search(r'\s+(?:v|vs\.?)\s+', part, re.IGNORECASE):
            clause = part
            break
    sides = [
        s.strip() for s in re.split(r'\s+(?:v|vs\.?)\s+', clause, flags=re.IGNORECASE)
    ]
    sides = [re.sub(r'\s*\([^)]*\)\s*$', '', s).strip() for s in sides]
    return sides if len(sides) == 2 and all(sides) else []


def expand_labels(question: str, labels: Sequence[str]) -> list[str]:
    """Replace short outcome labels with the longer name the question uses.

    A label expands when it names exactly one part of the question, by its
    words (``Raiders``) or by starting a word (``AST10``). Venue codes for the
    two teams of an ``A vs B`` question expand by position, only when the code
    could abbreviate that team (``WVIR``) or, for the last remaining team, starts
    with the same letter; anything less certain is left as it is.

    >>> expand_labels('NFL: Las Vegas Raiders v New England Patriots', ['Raiders', 'Patriots'])
    ['Las Vegas Raiders', 'New England Patriots']
    >>> expand_labels('Arizona vs. West Virginia', ['ARZ', 'WVIR'])
    ['Arizona', 'West Virginia']
    >>> expand_labels('Texans vs. Titans', ['HOU', 'TEN'])
    ['HOU', 'TEN']
    """
    segments = _segments(question)
    expanded: list[str | None] = []
    for label in labels:
        label_words = words(label)
        matches = [s for s in segments if label_words and label_words <= words(s)]
        matches = matches or [s for s in segments if labels_agree(label, s)]
        expanded.append(min(matches, key=len) if len(matches) == 1 else None)
    teams = _teams(question)
    if len(labels) == 2 and teams:
        for i in (0, 1):
            if (
                expanded[i] is None
                and abbreviates(labels[i], teams[i])
                and not abbreviates(labels[i], teams[1 - i])
            ):
                expanded[i] = teams[i]
        for i in (0, 1):
            other = expanded[1 - i]
            if (
                expanded[i] is None
                and other == teams[1 - i]
                and _letters(labels[i])[:1] == _letters(teams[i])[:1]
            ):
                expanded[i] = teams[i]
    return [
        e if e is not None else label for e, label in zip(expanded, labels, strict=True)
    ]


def _letters(text: str) -> str:
    return re.sub(r'[^a-z]', '', text.lower())


def _binary_subject(question: str) -> tuple[str, str]:
    """For ``League: Team A v Team B: Team A``, the subject and the event title."""
    parts = [p.strip() for p in question.split(':') if p.strip()]
    if len(parts) >= 3:
        return parts[-1], parts[-2]
    return '', ''


def as_polymarket_shape(market: Mapping[str, Any], venue: str) -> dict[str, Any]:
    """Kairos metadata for one Predict.fun or Hyperliquid market, as Gamma-style fields."""
    raw = market.get('raw') or {}
    listed = [o for o in market.get('outcomes') or [] if isinstance(o, Mapping)]
    labels = [str(o.get('outcome') or '') for o in listed]
    question = str(market.get('title') or raw.get('name') or '')
    lowered = [label.lower() for label in labels]
    totals = (
        len(lowered) == 2
        and lowered[0].startswith('over')
        and lowered[1].startswith('under')
    )
    if totals:
        outcomes = ['Over', 'Under']
    elif lowered == ['yes', 'no']:
        outcomes = labels
    else:
        outcomes = expand_labels(question, labels)
    category = market.get('category')
    if totals:
        sports_type = 'totals'
    elif re.search(r'spread|handicap', question, re.IGNORECASE):
        sports_type = 'spreads'
    elif category in ('Sports', 'Esports'):
        sports_type = 'moneyline'
    else:
        sports_type = ''
    event = str(market.get('event_id') or '')
    slug = event if '-' in event else ''
    status = str(market.get('status') or '').lower()
    subject = str(raw.get('yes_sub_title') or '')
    event_title = ''
    if lowered == ['yes', 'no'] and subject.strip().lower() in ('', 'yes'):
        # Hyperliquid puts the subject last: "League: Team A v Team B: Team A".
        subject, event_title = _binary_subject(question)
    return {
        'venue': venue,
        'id': str(market.get('market_id') or ''),
        'question': question,
        'outcomes': outcomes,
        'outcomeLabels': labels,
        'clobTokenIds': [str(o.get('token_id') or '') for o in listed],
        'conditionId': str(market.get('condition_id') or ''),
        'groupItemTitle': subject,
        'events': [{'title': event_title}] if event_title else [],
        'eventSlug': slug,
        'slug': slug,
        'sportsMarketType': sports_type,
        'eventDate': raw.get('expires_at') or market.get('end_date'),
        'endDate': market.get('end_date') or raw.get('expires_at'),
        'closed': status not in OPEN_STATUSES,
        'active': status in OPEN_STATUSES,
        'category': category,
        'feeRateBps': market.get('taker_base_fee_bps'),
        'tickSize': market.get('tick_size'),
    }
