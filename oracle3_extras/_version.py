"""The installed package version, read from its metadata like pymc-extras does."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version('oracle3-extras')
except PackageNotFoundError:  # a source checkout that was never installed
    __version__ = '0.0.0+unknown'
