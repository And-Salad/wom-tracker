"""The SQLite file itself: opening it, asking it things, keeping it current.

Everything that knows what a row means lives in the modules beside this one.
This holds the connection, the two ways of asking, and nothing about the
domain at all.
"""

import logging
import os
import sqlite3
import threading
from datetime import datetime, timedelta, timezone

from ..util import parse_api_time
from . import migrations
from .schema import FRESH_SECONDS, SCHEMA

log = logging.getLogger(__name__)

# Where a flattened metric finds its number: skills call it
# experience, bosses kills, activities score.
_VALUE_KEYS = ("experience", "kills", "score", "value")


class Store:
    """Small wrapper around a SQLite file; safe to use from several threads."""

    def __init__(self, path):
        self.path = path
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self._local = threading.local()
        conn = self.connect()
        # Whether this file is new decides whether the migrations have to run
        # at all: a database created from the current SCHEMA is at the current
        # version by construction, and nothing older can be true of it.
        fresh = not conn.execute(
            "SELECT COUNT(*) AS n FROM sqlite_master WHERE type='table'"
        ).fetchone()["n"]
        with conn:
            conn.executescript(SCHEMA)
        migrations.apply(conn, fresh=fresh)

    def connect(self):
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.path, timeout=30)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            self._local.conn = conn
        return conn

    def query(self, sql, params=()):
        return self.connect().execute(sql, params).fetchall()

    def query_one(self, sql, params=()):
        return self.connect().execute(sql, params).fetchone()


def _flatten(player_id, captured_at, data):
    for kind, key in (("skill", "skills"), ("boss", "bosses"),
                      ("activity", "activities"), ("computed", "computed")):
        section = data.get(key) or {}
        for metric, entry in section.items():
            if not isinstance(entry, dict):
                continue
            value = None
            for vk in _VALUE_KEYS:
                if entry.get(vk) is not None:
                    value = entry[vk]
                    break
            efficiency = entry.get("ehp")
            if efficiency is None:
                efficiency = entry.get("ehb")
            yield (
                player_id, kind, metric, captured_at,
                _num(value), _num(entry.get("rank")), _num(entry.get("level")),
                _num(efficiency),
            )


def _num(value):
    """Treat the API's -1 'unranked' sentinel as missing."""
    if value is None:
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return None if value < 0 else value


def _utcnow():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _origin(captured_at, fetched_at):
    """`poll` if our own request produced this reading, else `archive`."""
    made, got = parse_api_time(captured_at), parse_api_time(fetched_at)
    if made is None or got is None:
        return None
    return "poll" if (got - made).total_seconds() <= FRESH_SECONDS else "archive"


def _seconds_before(stamp, seconds):
    """`stamp` moved back by `seconds`, in the same ISO form."""
    when = parse_api_time(stamp) or datetime.now(timezone.utc)
    return (when - timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _days_ago(days):
    when = datetime.now(timezone.utc) - timedelta(days=days)
    return when.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
