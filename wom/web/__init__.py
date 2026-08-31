"""The web app: the dashboard people see, and the admin behind it.

The pages people are given are read-only. Everything that writes - the
settings, the prompts, and the buttons that start an update or a round of
summaries - sits under /admin behind a password.
"""

from .app import create_app

__all__ = ["create_app"]
