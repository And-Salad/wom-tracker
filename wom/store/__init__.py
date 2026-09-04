"""The SQLite store, one module per thing stored.

`Database` is still one object with one connection, because it is one file and
one transaction boundary, and every caller wants it that way. What it is not
any more is one 1400-line class that knew about players, readings, screenshots,
sessions, milestones, written round-ups and its own schema history at once.
Each of those has a module here, and Database is the sum of them.
"""

from .achievements import AchievementStore
from .core import Store
from .events import EventStore
from .images import ImageStore
from .maintenance import MaintenanceStore
from .players import PlayerStore
from .recaps import RecapStore
from .schema import SCHEMA
from .snapshots import SnapshotStore

__all__ = ["SCHEMA", "Database"]


class Database(PlayerStore, SnapshotStore, EventStore, ImageStore,
               AchievementStore, RecapStore, MaintenanceStore, Store):
    """Everything stored, over one SQLite file; safe to use from several threads."""
