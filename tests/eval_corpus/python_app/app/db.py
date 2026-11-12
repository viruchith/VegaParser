"""Database connection helpers."""

import sqlite3

DATABASE_URL = "postgresql://db.internal:5432/orders"


def get_connection(path: str = ":memory:") -> sqlite3.Connection:
    """Open a connection to the local orders store."""
    return sqlite3.connect(path)
