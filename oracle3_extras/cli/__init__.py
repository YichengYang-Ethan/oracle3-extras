"""Command-line entry point: ``oracle3-extras <group> <command>``.

Every command prints one JSON document on stdout so agents and scripts can read
it; progress messages go to stderr.
"""

from __future__ import annotations

import logging

import click

from oracle3_extras._version import __version__
from oracle3_extras.cli.kairos import kairos
from oracle3_extras.cli.metamask import metamask
from oracle3_extras.cli.scan import scan

__all__ = ['cli']


@click.group()
@click.version_option(__version__, prog_name='oracle3-extras')
@click.option('-q', '--quiet', is_flag=True, help='Hide progress messages.')
def cli(quiet: bool) -> None:
    """Integrations and experimental features for oracle3."""
    if quiet:
        logging.getLogger('oracle3_extras').setLevel(logging.WARNING)


cli.add_command(kairos)
cli.add_command(metamask)
cli.add_command(scan)
