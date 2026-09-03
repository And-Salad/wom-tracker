"""The web app: the dashboard people see, and the admin behind it.

The pages people are given are read-only. Everything that writes - the
settings, the prompts, and the buttons that start an update or a round of
summaries - sits under /admin behind a password.

With one exception, in hooks.py: a RuneLite plugin posts a line to us each
time one of the tracked accounts logs in. It has no password to offer, so
each player is given a URL that is itself the secret.
"""

from .app import create_app

__all__ = ["create_app"]
