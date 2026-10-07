"""``oracle3-extras kairos ...``: Kairos cross-venue pairs as oracle3 relations."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import click
from oracle3.market.relations import RELATIONS_PATH, MarketRelation

from oracle3_extras.arbitrage import scan_relations
from oracle3_extras.cli._common import echo_json, run, scan_options
from oracle3_extras.market.archive import load_archive, started_between, update_archive
from oracle3_extras.market.kairos import (
    MIN_SIMILARITY,
    VENUES,
    KairosRelations,
    check_settlements,
    history_summary,
    kairos_relations,
    price_history,
    save_relations,
    settlement_summary,
)

ARCHIVE = click.Path(dir_okay=False, path_type=Path)


@click.group()
@click.option(
    '--min-similarity',
    type=click.FloatRange(0.0, 1.0),
    default=None,
    help=f'Kairos similarity floor (Kairos never goes below {MIN_SIMILARITY}).',
)
@click.option(
    '--include-closed', is_flag=True, help='Keep pairs whose markets no longer trade.'
)
@click.option(
    '--venues',
    default=None,
    help=(
        'Comma-separated venues whose pairs to include [default: all four; '
        '"sync" writes kalshi,polymarket, the venues oracle3 prices].'
    ),
)
@click.pass_context
def kairos(
    ctx: click.Context,
    min_similarity: float | None,
    include_closed: bool,
    venues: str | None,
) -> None:
    """Kairos's cross-venue matched markets (https://kairos.trade) as oracle3 relations.

    Covers every venue Kairos matches: Kalshi, Polymarket, Predict.fun and
    Hyperliquid. Uses Kairos's public APIs; set KAIROS_CLIENT_ID,
    KAIROS_API_KEY and KAIROS_API_SECRET for higher rate limits and to size
    Predict.fun trades with Kairos fee quotes.
    """
    chosen = None
    if venues is not None:
        chosen = tuple(v.strip().lower() for v in venues.split(',') if v.strip())
        unknown = sorted(set(chosen) - set(VENUES))
        if unknown or len(chosen) < 2:
            raise click.BadParameter(
                f'pick at least two of {", ".join(VENUES)}', param_hint='--venues'
            )
    ctx.obj = {
        'min_similarity': min_similarity,
        'include_closed': include_closed,
        'venues': chosen,
    }


#: The venues oracle3's own tools price; ``sync`` writes only their pairs by default.
ORACLE3_VENUES = ('kalshi', 'polymarket')


def _load(
    options: dict[str, Any], default: tuple[str, ...] = VENUES
) -> KairosRelations:
    return run(
        kairos_relations(
            min_similarity=options['min_similarity'],
            include_closed=options['include_closed'],
            venues=options['venues'] or default,
        )
    )


def _row(relation: MarketRelation) -> dict[str, Any]:
    a, b = relation.market_a, relation.market_b
    outcomes = b.get('outcomes') or []
    index = 0 if relation.spread_type == 'same_event' else 1
    first = a.get('yes_outcome') or (a.get('outcomes') or [None])[0]
    return {
        'relation_id': relation.relation_id,
        'relation': relation.spread_type,
        'similarity': relation.confidence,
        'categories': relation.analysis_b.get('kairos_categories', []),
        'market_a': {
            'venue': a.get('venue'),
            'market_id': a.get('market_id'),
            'name': a.get('name'),
            'first_outcome': first,
        },
        'market_b': {
            'venue': b.get('venue'),
            'market_id': b.get('market_id'),
            'name': b.get('name'),
            'matching_outcome': outcomes[index] if index < len(outcomes) else None,
        },
        'evidence': relation.analysis_b.get('evidence', []),
        'warnings': relation.analysis_b.get('warnings', []),
        'valid_until': relation.valid_until,
    }


@kairos.command()
@click.option(
    '--rejected',
    'show_rejected',
    is_flag=True,
    help='Also list pairs that were not aligned.',
)
@click.option(
    '--limit', type=int, default=None, help='Relations to print (default all).'
)
@click.pass_obj
def pairs(options: dict[str, Any], show_rejected: bool, limit: int | None) -> None:
    """List Kairos pairs with their outcomes lined up on both venues."""
    result = _load(options)
    payload: dict[str, Any] = {
        'ok': True,
        'summary': result.summary(),
        'relations': [_row(r) for r in result.relations[:limit]],
    }
    if show_rejected:
        payload['rejected'] = [
            {
                'a': {
                    'venue': pair.a.provider,
                    'market_id': pair.a.market_id,
                    'title': pair.a.title,
                },
                'b': {
                    'venue': pair.b.provider,
                    'market_id': pair.b.market_id,
                    'title': pair.b.title,
                },
                'similarity': pair.similarity,
                'reason': reason,
            }
            for pair, reason in result.rejected
        ]
    echo_json(payload)


@kairos.command()
@click.option(
    '--store',
    type=click.Path(dir_okay=False, path_type=Path),
    default=RELATIONS_PATH,
    show_default=True,
    help='oracle3 relation store to update.',
)
@click.option(
    '--no-prune',
    is_flag=True,
    help='Keep Kairos relations that dropped out of the catalog.',
)
@click.option('--dry-run', is_flag=True, help='Align the pairs but write nothing.')
@click.pass_obj
def sync(options: dict[str, Any], store: Path, no_prune: bool, dry_run: bool) -> None:
    """Write aligned pairs into an oracle3 relation store.

    oracle3's list_relations and check_constraint_live tools read the store, so
    agents can use the pairs right away. Relations keep their status and
    validation across syncs. oracle3 prices Kalshi and Polymarket, so only
    their pairs are written unless --venues names others.
    """
    result = _load(options, ORACLE3_VENUES)
    written = (
        None if dry_run else save_relations(result.relations, store, prune=not no_prune)
    )
    echo_json(
        {
            'ok': True,
            'store': str(store),
            'dry_run': dry_run,
            'summary': result.summary(),
            'written': written,
        }
    )


@kairos.command()
@scan_options
@click.option(
    '--max-confirm',
    type=click.IntRange(min=0),
    default=50,
    show_default=True,
    help='Predict.fun pairs to confirm with Kairos fee quotes (largest edges first).',
)
@click.pass_obj
def scan(
    options: dict[str, Any],
    contracts: float,
    maker: bool,
    max_contracts: float | None,
    min_edge: float,
    top: int,
    no_depth: bool,
    max_confirm: int,
) -> None:
    """Check every aligned pair for a cross-venue edge after fees (read-only)."""
    result = _load(options)
    report = run(
        scan_relations(
            result.relations,
            contracts=contracts,
            maker=maker,
            depth=not no_depth,
            max_contracts=max_contracts,
            min_edge=min_edge,
            max_confirm=max_confirm,
        )
    )
    echo_json({'ok': True, 'catalog': result.summary(), **report.to_dict(top=top)})


@kairos.command()
@click.option(
    '--archive',
    type=ARCHIVE,
    required=True,
    help='Archive file to create or update (.jsonl.gz).',
)
@click.option(
    '--keep-days',
    type=float,
    default=14,
    show_default=True,
    help='Forget pairs whose event started longer ago than this.',
)
@click.pass_obj
def snapshot(options: dict[str, Any], archive: Path, keep_days: float) -> None:
    """Archive today's aligned pairs, so they outlive the Kairos catalog.

    Kairos drops a pair once its markets expire; history and settlements read
    the archive instead. Run it a few times a day.
    """
    result = _load(options)
    counts = update_archive(archive, result.relations, keep_days=keep_days)
    echo_json(
        {
            'ok': True,
            'archive': str(archive),
            'summary': result.summary(),
            'archived': counts,
        }
    )


@kairos.command()
@click.option(
    '--archive',
    type=ARCHIVE,
    required=True,
    help='Archive written by `kairos snapshot`.',
)
@click.option(
    '--days',
    type=float,
    default=1,
    show_default=True,
    help='Events that started in the last N days.',
)
@click.option(
    '--timeframe',
    type=click.Choice(['60', '300', '900', '3600']),
    default='60',
    show_default=True,
    help='Candle width in seconds.',
)
@click.option(
    '--widest', type=int, default=10, show_default=True, help='Pairs to list.'
)
@click.pass_obj
def history(
    options: dict[str, Any], archive: Path, days: float, timeframe: str, widest: int
) -> None:
    """How far apart the two venues traded each archived pair around its start.

    Uses Kairos candles: trade prices in the buckets where both venues traded,
    not executable quotes.
    """
    now = datetime.now(timezone.utc)
    relations = started_between(load_archive(archive), now - timedelta(days=days), now)
    histories = run(price_history(relations, timeframe=int(timeframe), now=now))
    echo_json(
        {
            'ok': True,
            'archive': str(archive),
            'days': days,
            'summary': history_summary(histories, widest=widest),
        }
    )


@kairos.command()
@click.option(
    '--archive',
    type=ARCHIVE,
    required=True,
    help='Archive written by `kairos snapshot`.',
)
@click.option(
    '--days',
    type=float,
    default=7,
    show_default=True,
    help='Events that started in the last N days.',
)
@click.option(
    '--min-age-hours',
    type=float,
    default=6,
    show_default=True,
    help='Skip events that started more recently than this (still settling).',
)
@click.pass_obj
def settlements(
    options: dict[str, Any], archive: Path, days: float, min_age_hours: float
) -> None:
    """Check that both venues settled each archived pair the same way."""
    now = datetime.now(timezone.utc)
    relations = started_between(
        load_archive(archive),
        now - timedelta(days=days),
        now - timedelta(hours=min_age_hours),
    )
    checks = run(check_settlements(relations))
    echo_json(
        {
            'ok': True,
            'archive': str(archive),
            'days': days,
            'summary': settlement_summary(checks),
        }
    )
