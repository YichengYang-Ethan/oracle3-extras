"""``oracle3-extras scan``: check stored oracle3 relations against live prices."""

from __future__ import annotations

from pathlib import Path

import click
from oracle3.market.relations import RELATIONS_PATH, RelationStore

from oracle3_extras.arbitrage import scan_relations
from oracle3_extras.cli._common import echo_json, run, scan_options


@click.command()
@click.option(
    '--store',
    type=click.Path(dir_okay=False, path_type=Path),
    default=RELATIONS_PATH,
    show_default=True,
    help='oracle3 relation store to read.',
)
@click.option(
    '--source',
    default=None,
    help="Only relations whose id starts with this prefix, for example 'kairos'.",
)
@click.option(
    '--status',
    type=click.Choice(['discovered', 'validated', 'deployed']),
    default=None,
    help='Only relations at this lifecycle status.',
)
@scan_options
def scan(
    store: Path,
    source: str | None,
    status: str | None,
    contracts: float,
    maker: bool,
    max_contracts: float | None,
    min_edge: float,
    top: int,
    no_depth: bool,
) -> None:
    """Check stored relations against live Kalshi and Polymarket prices (read-only)."""
    relations = RelationStore(store).list(status=status) if store.exists() else []
    if source:
        relations = [r for r in relations if r.relation_id.startswith(f'{source}:')]
    report = run(
        scan_relations(
            relations,
            contracts=contracts,
            maker=maker,
            depth=not no_depth,
            max_contracts=max_contracts,
            min_edge=min_edge,
        )
    )
    echo_json({'ok': True, 'store': str(store), **report.to_dict(top=top)})
