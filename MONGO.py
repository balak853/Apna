"""MongoDB connection helpers for ff-bot2.

MONGO_URI must be provided through the environment. No credentials are stored
in this module or in the repository.
"""

from __future__ import annotations

import os
from typing import Any

from pymongo import MongoClient

DEFAULT_DATABASE = "ff_bot"
SERVER_SELECTION_TIMEOUT_MS = 10_000


def get_mongo_uri() -> str:
    """Return the configured MongoDB URI or raise a clear configuration error."""
    uri = os.environ.get("MONGO_URI", "").strip()
    if not uri:
        raise RuntimeError("MONGO_URI environment variable is not set")
    return uri


def get_database_name() -> str:
    """Return the configured database name."""
    return os.environ.get("MONGO_DATABASE", DEFAULT_DATABASE).strip() or DEFAULT_DATABASE


def connect() -> tuple[MongoClient[Any], Any]:
    """Connect to MongoDB, verify the server, and return client plus database."""
    client: MongoClient[Any] = MongoClient(
        get_mongo_uri(),
        serverSelectionTimeoutMS=SERVER_SELECTION_TIMEOUT_MS,
    )
    client.admin.command("ping")
    return client, client[get_database_name()]


def get_collections(database: Any) -> dict[str, Any]:
    """Return all collections used by the converted bot."""
    return {
        "users": database.users,
        "groups": database.groups,
        "check": database.check,
        "paid": database.paid,
        "processed_updates": database.processed_updates,
        "migration_meta": database.migration_meta,
        "runtime": database.runtime,
    }


def check_connection(client: MongoClient[Any]) -> bool:
    """Return True only when MongoDB responds to a ping."""
    try:
        client.admin.command("ping")
        return True
    except Exception:
        return False
