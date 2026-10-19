"""``oracle3-extras tickers``: open prediction markets that price public companies."""

from __future__ import annotations

import click

from oracle3_extras.cli._common import echo_json, run
from oracle3_extras.market.tickers import EVENT_TYPES, Company, sp500, ticker_markets

VENUE_CHOICES = ('kalshi', 'polymarket')


def parse_companies(arguments: tuple[str, ...]) -> list[Company] | None:
    """``NVDA`` or ``RKLB=Rocket Lab`` (``META=Meta|Facebook`` for several names)."""
    if not arguments:
        return None
    known = {c.ticker: c for c in sp500()}
    companies = []
    for argument in arguments:
        ticker, _, names = argument.partition('=')
        ticker = ticker.strip().upper()
        if not ticker:
            raise click.BadParameter(f'no ticker in {argument!r}', param_hint='TICKERS')
        listed = tuple(n.strip() for n in names.split('|') if n.strip())
        base = known.get(ticker)
        if listed:
            companies.append(
                Company(
                    ticker=ticker,
                    name=base.name if base else listed[0],
                    names=listed,
                    sector=base.sector if base else '',
                )
            )
        else:
            companies.append(base or Company(ticker=ticker, name=ticker))
    return companies


@click.command()
@click.argument('tickers', nargs=-1)
@click.option(
    '--venues',
    default=','.join(VENUE_CHOICES),
    show_default=True,
    help='Comma-separated venues to search.',
)
@click.option(
    '--type',
    'types',
    multiple=True,
    type=click.Choice(EVENT_TYPES),
    help='Keep this event type (repeatable).',
)
@click.option(
    '--min-confidence',
    type=click.FloatRange(0.0, 1.0),
    default=0.0,
    show_default=True,
    help='1.0 keeps only tickers written in the market title.',
)
@click.option(
    '--no-prices',
    is_flag=True,
    help='Skip Kalshi prices (one request per Kalshi event).',
)
@click.option('--limit', type=int, default=None, help='Events to print (default all).')
def tickers(
    tickers: tuple[str, ...],
    venues: str,
    types: tuple[str, ...],
    min_confidence: float,
    no_prices: bool,
    limit: int | None,
) -> None:
    """Open Kalshi and Polymarket events that price companies, by ticker (read-only).

    TICKERS are stock tickers, optionally with the names to look for:
    NVDA, "RKLB=Rocket Lab", "META=Meta|Facebook". Without TICKERS: the S&P 500,
    plus every ticker Polymarket writes in a finance market's title.
    """
    chosen = tuple(v.strip().lower() for v in venues.split(',') if v.strip())
    if not chosen or set(chosen) - set(VENUE_CHOICES):
        raise click.BadParameter(
            f'pick from {", ".join(VENUE_CHOICES)}', param_hint='--venues'
        )
    result = run(
        ticker_markets(
            parse_companies(tickers),
            venues=chosen,
            types=types or None,
            min_confidence=min_confidence,
            prices=not no_prices,
        )
    )
    echo_json(
        {
            'ok': True,
            'summary': result.summary(),
            'events': [e.to_dict() for e in result.events[:limit]],
        }
    )
