"""Orchpact: a self-hosted organization workflow runtime."""
from importlib.metadata import PackageNotFoundError,version

try:
    __version__=version('orchpact')
except PackageNotFoundError:
    __version__='0+uninstalled'
