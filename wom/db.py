"""SQLite storage for players, snapshots and the flattened metric history.

Snapshots are kept whole (as JSON) so nothing from the API is lost, and are
also flattened into `metrics` so charts and tables can query them with SQL.

The implementation is the wom.store package beside this file; this is the name
the rest of the app has always imported and still does.
"""

from .store import SCHEMA, Database

__all__ = ["SCHEMA", "Database"]
