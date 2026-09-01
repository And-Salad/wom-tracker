"""Maintenance jobs for the tracker, for when the schedule is not what you want.

    py wom_tracker.py --update     run one update pass, then exit
    py wom_tracker.py --backfill   re-import every player's stored history
    py wom_tracker.py --summarize  write the Claude summaries for each player
    py wom_tracker.py --compact    thin old history to one snapshot a day
    py wom_tracker.py --list       print the tracked usernames

The server (web_app.py --with-scheduler) does the updating and the summaries on
its own; these are the same jobs run by hand. Against a hosted deployment, run
them where the database is:

    fly ssh console -a wom-tracker -C "python /app/wom_tracker.py --compact"

History is imported automatically the first time a player is seen, so
--backfill is only needed to pull it again after clearing the database.
"""

import argparse
import logging
import os
import sys

from wom.api import WomClient
from wom.logs import setup_logging
from wom.config import Config, DB_PATH
from wom.db import Database
from wom.scheduler import stamp_now
from wom.updater import backfill_player, update_all

log = logging.getLogger("wom")




def run_headless_update():
    config = Config()
    names = config.get("usernames", [])
    if not names:
        print("No usernames configured. Open the app and add some under Options.")
        return 1
    database = Database(DB_PATH)
    client = WomClient(config.get("api_key", ""), config.get("user_agent_contact", ""))
    results = update_all(client, database, names, trigger="cli")
    for result in results:
        print("{:<14} {}".format(result.username, result.message if result.ok
                                 else "FAILED - " + result.message))
    failed = sum(1 for r in results if not r.ok)
    config["last_run"] = stamp_now()
    config.save()
    extras = []
    imported = sum(r.imported for r in results)
    if imported:
        extras.append("{} historic snapshots imported".format(imported))
    milestones = sum(r.milestones for r in results)
    if milestones:
        extras.append("{} new milestones".format(milestones))
    print("{} updated, {} failed{}".format(
        len(results) - failed, failed, (", " + ", ".join(extras)) if extras else ""))
    return 1 if failed else 0


def run_backfill(names=None):
    """Re-import stored history for every tracked player, ignoring the once-only flag."""
    config = Config()
    names = names or config.get("usernames", [])
    if not names:
        print("No usernames configured. Open the app and add some under Options.")
        return 1
    database = Database(DB_PATH)
    client = WomClient(config.get("api_key", ""), config.get("user_agent_contact", ""))
    total = 0
    for name in names:
        imported, note = backfill_player(client, database, name, force=True)
        total += imported
        print("{:<16} {}".format(name, note or "nothing to import"))
    print("{} snapshots imported".format(total))
    return 0


def run_compact(keep_days, dry_run):
    """Thin stored history so long-term growth stays flat."""
    database = Database(DB_PATH)
    before = os.path.getsize(DB_PATH)
    preview = database.compaction_preview(keep_days)
    print("{:,} snapshots stored; {:,} beyond the last {} days are more than"
          " one a day".format(preview["total"], preview["removable"], keep_days))
    if dry_run:
        print("dry run - nothing removed")
        return 0
    if not preview["removable"]:
        print("nothing to compact")
        return 0
    result = database.compact_snapshots(keep_days)
    after = os.path.getsize(DB_PATH)
    print("removed {:,} snapshots; database {:.1f} MB -> {:.1f} MB".format(
        result["removed"], before / 1e6, after / 1e6))
    return 0


def run_summaries(period_keys, only_player, force, dry_run, show_prompt,
                  due_only=False):
    """Generate the written summaries, or price them up without spending."""
    from wom import periods, summaries
    config = Config()
    if show_prompt:
        from wom import periods as _periods
        for kind, title in (("player", "PER-PLAYER NOTES"),
                            ("group", "GROUP ROUND-UP")):
            base = summaries.base_prompt_path(kind)
            summaries.load_prompt(config, kind=kind)   # create it if missing
            print("== {} ==".format(title))
            print("base prompt (used unless a period has its own):")
            print("    {}".format(base))
            print("per-period overrides - create any of these to differ:")
            for key in _periods.SUMMARY_PERIODS:
                path = summaries.period_prompt_path(key, kind)
                print("        {:<7} {}  {}".format(
                    key, path,
                    "IN USE" if os.path.exists(path) else "(not present)"))
            print()
            print(open(base, encoding="utf-8").read().rstrip())
            print()
        return 0

    database = Database(DB_PATH)
    players = database.players()
    if only_player:
        wanted = only_player.lower()
        players = [p for p in players if p["username"] == wanted]
        if not players:
            print("no tracked player called {!r}".format(only_player))
            return 1
    if not players:
        print("no players stored yet - run an update first")
        return 1
    if due_only:
        keys = summaries.due_periods(database)
        if not keys:
            print("nothing due yet - every window that has closed is written")
            return 0
        print("due right now: {}".format(", ".join(keys)))
    else:
        keys = period_keys or ["day"]
        unknown = [k for k in keys if k not in periods.SUMMARY_PERIODS]
        if unknown:
            print("summaries only cover {} - not {}".format(
                ", ".join(periods.SUMMARY_PERIODS), ", ".join(unknown)))
            return 1

    if dry_run:
        total = 0.0
        # The group round-up is one extra call per window, so price it too.
        for key in keys:
            window = periods.latest_window(key)
            system = summaries.load_prompt(config, key, kind="group")
            digest = summaries.build_group_digest(
                database, config, database.players(), window)
            try:
                tokens, cost = summaries.estimate(config, system, digest)
            except summaries.SummaryError as exc:
                print(exc)
                return 1
            total += cost
            print("=" * 68)
            print("Group - {}   ~{} input tokens, up to ${:.4f}".format(
                window.label, tokens, cost))
            print("=" * 68)
            print(digest)
            print()
        for player in players:
            for key in keys:
                window = periods.latest_window(key)
                system = summaries.load_prompt(config, key)
                digest = summaries.build_digest(database, config, player, window)
                try:
                    tokens, cost = summaries.estimate(config, system, digest)
                except summaries.SummaryError as exc:
                    print(exc)
                    return 1
                total += cost
                print("=" * 68)
                print("{} - {}   ~{} input tokens, up to ${:.4f}".format(
                    player["display_name"], window.label, tokens, cost))
                print("=" * 68)
                print(digest)
                print()
        print("nothing was sent. Worst case for all of the above: ${:.3f}".format(total))
        return 0

    results = summaries.summarise_all(
        database, config, players, keys, force=force,
        progress=lambda e: print("{:<14} {:<8} {}".format(
            e["player"], e["period"], e["note"])))
    failed = sum(1 for r in results if r.get("failed"))
    return 1 if failed else 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--update", action="store_true",
                        help="run one update pass without opening the window")
    parser.add_argument("--backfill", nargs="*", metavar="NAME",
                        help="re-import stored history, for the given names or all of them")
    parser.add_argument("--summarize", "--summarise", dest="summarize",
                        action="store_true", help="write the Claude summaries")
    parser.add_argument("--period", action="append", metavar="KEY",
                        help="which period to summarise (repeatable); default week")
    parser.add_argument("--player", metavar="NAME", help="summarise just this player")
    parser.add_argument("--due", action="store_true",
                        help="with --summarize, write exactly what the schedule owes now")
    parser.add_argument("--force", action="store_true",
                        help="regenerate even when the data has not changed")
    parser.add_argument("--show-prompt", action="store_true",
                        help="print the tunable summary prompt and its path")
    parser.add_argument("--compact", action="store_true",
                        help="thin history older than --keep-days to one snapshot a day")
    parser.add_argument("--keep-days", type=int, default=30, metavar="N",
                        help="how much recent history --compact leaves untouched")
    parser.add_argument("--dry-run", action="store_true",
                        help="report what would happen without doing it")
    parser.add_argument("--list", action="store_true", help="print the tracked usernames")
    parser.add_argument("--verbose", action="store_true", help="debug logging")
    args = parser.parse_args(argv)

    # Its own file: the server holds wom-web.log open, and on Windows a
    # rotation cannot rename a file another process has open.
    setup_logging(args.verbose, role="cli")

    if args.list:
        for name in Config().get("usernames", []):
            print(name)
        return 0
    if args.summarize or args.show_prompt:
        return run_summaries(args.period, args.player, args.force,
                             args.dry_run, args.show_prompt, args.due)
    if args.compact:
        return run_compact(args.keep_days, args.dry_run)
    if args.backfill is not None:
        return run_backfill(args.backfill or None)
    if args.update:
        return run_headless_update()

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
