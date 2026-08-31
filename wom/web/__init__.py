"""A read-only web view of the tracked data.

Deliberately read-only: the desktop app owns the config (including the API
key) and is the only thing that writes. This server opens the same database,
renders the same charts, and never exposes a way to change anything.
"""

from .app import create_app

__all__ = ["create_app"]
