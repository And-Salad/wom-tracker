"""A token that moves whenever something a page shows may have changed.

Open pages poll /api/status and refresh themselves when its `stamp` moves.
That stamp used to be the time of the last update run, so it only moved when
a run finished - and a run was not the only thing that changed what pages
show. A Dink event lands a boss count or a screenshot straight away, a round
of summaries finishes minutes after the run it followed, a history import
fills charts in a page at a time, and midnight turns "today" into yesterday.
None of those moved the stamp, so each sat unseen until the next run or a
reload.

So this counts changes instead, in memory. It is one process - the server
runs the schedule, the hooks and the admin jobs - and a counter costs the
status endpoint nothing, which matters because every open tab asks it every
minute. A restart starts a new count under a new boot token, which reads as
one change and costs each open page one refresh.
"""

import logging
import threading
import uuid

log = logging.getLogger(__name__)

_lock = threading.Lock()
_count = 0
_boot = uuid.uuid4().hex[:8]


def bump(reason=""):
    """Note that something visible changed."""
    global _count
    with _lock:
        _count += 1
    if reason:
        log.debug("change %d: %s", _count, reason)


def token():
    """This process's count of changes, unique across restarts."""
    with _lock:
        return "{}.{}".format(_boot, _count)
