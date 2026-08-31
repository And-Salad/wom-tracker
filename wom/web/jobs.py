"""Long jobs the admin page starts, run off the request thread.

An update pass over six players takes tens of seconds and a round of summaries
longer still; neither can happen inside a request without the browser giving
up. Each job runs on its own thread and reports progress here, which the admin
page polls. One at a time, deliberately: two concurrent update passes would
race each other through the same rows for no benefit.
"""

import logging
import threading
from datetime import datetime, timezone

log = logging.getLogger(__name__)


class Job:
    """One background task, and whatever it has managed to say so far."""

    def __init__(self, name):
        self.name = name
        self.started_at = datetime.now(timezone.utc)
        self.finished_at = None
        self.note = "starting..."
        self.lines = []
        self.failed = False

    @property
    def running(self):
        return self.finished_at is None

    def say(self, note, keep=False):
        self.note = note
        if keep:
            self.lines.append(note)
            del self.lines[:-200]      # a run is bounded; the log need not be

    def finish(self, note, failed=False):
        self.note = note
        self.failed = failed
        self.finished_at = datetime.now(timezone.utc)

    def as_dict(self):
        return {
            "name": self.name,
            "running": self.running,
            "failed": self.failed,
            "note": self.note,
            "lines": list(self.lines),
            "started": self.started_at.isoformat(),
        }


class JobRunner:
    """Holds the one job that may be in flight, and the last one that ran."""

    def __init__(self):
        self._lock = threading.Lock()
        self.current = None
        self.last = None

    @property
    def busy(self):
        with self._lock:
            return self.current is not None and self.current.running

    def start(self, name, work):
        """Run `work(job)` on a thread. Returns the Job, or None if busy."""
        with self._lock:
            if self.current is not None and self.current.running:
                return None
            job = Job(name)
            self.current = job

        def run():
            try:
                work(job)
                if job.running:
                    job.finish("done")
            except Exception as exc:
                log.exception("background job %s failed", name)
                job.finish("failed: {}".format(exc), failed=True)
            finally:
                with self._lock:
                    self.last = job

        threading.Thread(target=run, name="wom-job-" + name, daemon=True).start()
        return job

    def status(self):
        with self._lock:
            job = self.current or self.last
        return job.as_dict() if job else {"name": None, "running": False,
                                          "note": "nothing has run yet",
                                          "lines": [], "failed": False}
