"""What is actually running here.

A deploy that has gone out and a deploy that has landed look identical from
the outside: the page answers either way, because the old one was answering
too. So the running process says which commit it is, and the admin page prints
it - "did my change ship" becomes something to read rather than something to
infer from the clock.

The commit arrives as an environment variable baked in at image build time
(see the Dockerfile), because a container has no git and no repository to ask.
Running from a clone there is no such variable, so it asks git directly -
which is the case where the answer matters least and is easiest to get.
"""

import logging
import os
import subprocess
from datetime import datetime, timezone

from .util import fmt_ago

log = logging.getLogger(__name__)

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Set by the Dockerfile from a --build-arg. Empty anywhere else.
BUILD_ENV = "WOM_BUILD_SHA"

# When this process started, which is the other half of the answer. The commit
# says what code is running; this says whether it is a process that started
# after the deploy or the one that was already there. A machine restarted
# without a deploy moves this and not the commit, and that is worth being able
# to tell apart.
STARTED_AT = datetime.now(timezone.utc)

# Cached because the fallback shells out, and this is read on every render of
# the admin page. Neither answer can change without the process restarting.
_resolved = None


def _from_git():
    """The checked-out commit, for a run from a clone rather than an image."""
    try:
        found = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=APP_DIR,
            capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return ""                      # no git, or no repository: not an error
    return found.stdout.strip() if found.returncode == 0 else ""


def sha():
    """The commit this is running, or "" when it cannot be known."""
    global _resolved
    if _resolved is None:
        _resolved = (os.environ.get(BUILD_ENV) or "").strip() or _from_git()
    return _resolved


def forget():
    """Resolve the commit again on the next call. For tests."""
    global _resolved
    _resolved = None


def info(started_at=None):
    """What the admin page prints: which commit, and how long it has been up."""
    began = started_at or STARTED_AT
    found = sha()
    return {
        "sha": found,
        # Seven is what git itself abbreviates to, and what a person compares
        # against the commit list without reading every character.
        "short": found[:7],
        "started_at": began.isoformat(),
        "ago": fmt_ago(began.isoformat()),
    }
