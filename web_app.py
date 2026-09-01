"""Serve the tracked data as a read-only web dashboard.

    py web_app.py                       http://localhost:8000, this machine only
    py web_app.py --host 0.0.0.0        reachable from the rest of your network
    py web_app.py --port 9000

The public pages are read-only; everything that changes anything lives under
/admin behind WOM_ADMIN_PASSWORD, and is not registered at all without one.

Pass --with-scheduler to have this process run the updates and the
summaries as well as serve the pages. That is how it runs hosted, and it is
the only thing that should be running the schedule - two of them would update
the same database twice.
"""

import argparse
import logging
import socket
import sys
from datetime import datetime, timezone

from wom.config import Config
from wom.logs import setup_logging
from wom.web import create_app

log = logging.getLogger("wom.web")


def local_addresses(port):
    """Addresses worth printing so people know where to point a browser."""
    out = ["http://localhost:{}".format(port)]
    try:
        host = socket.gethostbyname_ex(socket.gethostname())[2]
    except OSError:
        host = []
    return out + ["http://{}:{}".format(ip, port) for ip in host
                  if not ip.startswith("127.")]


# How much recent history stays at full resolution. Beyond it, each day keeps
# its last reading and each metric its last change of that day.
COMPACT_KEEP_DAYS = 30


def _thin_history(database, settings):
    """Compact old history once a day, on the first run after midnight.

    Six readings an hour is the right resolution for this week and far more
    than a month-wide chart can draw. Nothing had ever called this: it was a
    command somebody had to remember, which is a thing that does not happen.
    """
    from wom.scheduler import zone
    today = datetime.now(timezone.utc).astimezone(zone()).strftime("%Y-%m-%d")
    if settings.get("last_compact") == today:
        return
    result = database.compact_snapshots(keep_days=COMPACT_KEEP_DAYS)
    settings["last_compact"] = today
    settings.save()
    if result.get("removed"):
        log.info("thinned %d old readings", result["removed"])


def start_scheduler(app):
    """Run the update schedule from inside the server process."""
    from wom.api import WomClient
    from wom.scheduler import SlotScheduler, wants_achievements
    from wom.summaries import maybe_write_summaries
    from wom.updater import update_all

    config = Config()

    def job(trigger):
        settings = Config()
        client = WomClient(settings.get("api_key", ""),
                           settings.get("user_agent_contact", ""))
        database = app.config["DATABASE"]
        # Milestones move rarely and cost a request per player. At a run every
        # ten minutes that is worth doing on the hour rather than six times an
        # hour, which halves what the run asks of Wise Old Man.
        update_all(client, database, settings.get("usernames", []),
                   trigger=trigger, achievements=wants_achievements())
        # The summaries a closed window owes ride on the back of an update, so
        # this has to happen here or they never get written at all.
        try:
            maybe_write_summaries(database, settings)
        except Exception:
            log.exception("writing the scheduled summaries failed")
        try:
            _thin_history(database, settings)
        except Exception:
            log.exception("thinning old history failed")

    scheduler = SlotScheduler(config, job)
    # The admin page's buttons take the same "something is running" flag, so a
    # scheduled slot cannot fire into the middle of a manual run.
    app.config["SCHEDULER"] = scheduler
    scheduler.start()
    log.info("update scheduler running inside the web server")
    return scheduler


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--host", default="127.0.0.1",
                        help="0.0.0.0 to allow other machines on your network")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--with-scheduler", action="store_true",
                        help="also run the update schedule from this process")
    parser.add_argument("--debug", action="store_true",
                        help="Flask's dev server with tracebacks, localhost only")
    args = parser.parse_args(argv)

    setup_logging(role="web")
    app = create_app()
    if args.with_scheduler:
        start_scheduler(app)

    print("WOM Tracker - read-only dashboard")
    for url in local_addresses(args.port):
        print("   ", url)
    if args.host == "127.0.0.1":
        print("    (this machine only; pass --host 0.0.0.0 to share on your network)")
    print("Ctrl+C to stop.")

    if args.debug:
        # The reloader forks a second process, which would double any
        # scheduler running here, so it stays off.
        app.run(host="127.0.0.1", port=args.port, debug=True, use_reloader=False)
    else:
        from waitress import serve
        serve(app, host=args.host, port=args.port, threads=8)
    return 0


if __name__ == "__main__":
    sys.exit(main())
