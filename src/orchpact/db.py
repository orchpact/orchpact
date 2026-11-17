"""Shared storage API. Schema ownership is exclusively bootstrap."""
from .adapters.database import Database, Connection, ident, now, dumps, loads
