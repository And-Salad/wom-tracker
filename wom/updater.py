"""Runs one update pass over the tracked username list."""

import logging
from datetime import datetime, timedelta, timezone

from . import scheduler, sessions
from .util import parse_api_time
from .api import HISTORY_LIMIT, WomError

log = logging.getLogger(__name__)


class PlayerResult:
    """What one player's pass did. Deliberately small: these travel through a
    queue to the UI thread, so they must not pin whole API payloads in memory."""

    def __init__(self, username, ok, message="", imported=0, milestones=0):
        self.username = username
        self.ok = ok
        self.message = message
        self.imported = imported      # historic snapshots pulled in on this pass
        self.milestones = milestones  # achievements seen for the first time

    def __repr__(self):
        return "<PlayerResult {} {}>".format(self.username, "ok" if self.ok else "failed")


# How far back a run reconsiders session attribution. Long enough that a
# logout arriving late still corrects the reading it belongs to, short enough
# that this is a handful of rows every ten minutes rather than a sweep.
SESSION_LOOKBACK_DAYS = 3


def _place_sessions(database, usernames):
    """Credit each session's gain to the time it was earned in.

    Here rather than in the caller because all three entry points - the
    server, the admin button and the command line - go through update_all,
    and a correction that only some of them applied would be worse than none.

    Never allowed to break a run. The readings are the thing worth having;
    this only decides where to file them.
    """
    since = (datetime.now(timezone.utc) - timedelta(days=SESSION_LOOKBACK_DAYS)
             ).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    written = 0
    for username in usernames:
        player = database.player_by_username(username)
        if player is None:
            continue
        try:
            written += sessions.attribute(database, scheduler.zone(), player, since)
        except Exception:
            log.exception("placing sessions for %s failed", username)
    if written:
        log.info("session attribution wrote %d interpolated values", written)
    return written


def update_all(client, database, usernames, trigger="manual", progress=None,
               starting=None, cancelled=None, achievements=True):
    """Refresh every username in turn, saving each result as it arrives.

    `starting(index, total, username)` fires before each player and
    `progress(index, total, PlayerResult)` after it, so a UI can show live
    feedback across the slow bits - importing history takes several calls.
    `cancelled()` is polled to allow an early stop. `achievements=False` skips
    the milestone fetch, which is a request per player for something that
    moves rarely - at a run every ten minutes it is worth doing hourly rather
    than every time. Returns the results.
    """
    usernames = list(usernames)
    total = len(usernames)
    run_id = database.start_run(trigger, roster=total)
    results = []

    for index, username in enumerate(usernames, start=1):
        if cancelled is not None and cancelled():
            log.info("update run cancelled after %d/%d", index - 1, total)
            break
        if starting is not None:
            try:
                starting(index, total, username)
            except Exception:
                log.exception("starting callback failed")
        result = update_one(client, database, username, achievements)
        results.append(result)
        if progress is not None:
            try:
                progress(index, total, result)
            except Exception:  # a UI callback must never break the run
                log.exception("progress callback failed")

    ok = sum(1 for r in results if r.ok)
    failed = [r for r in results if not r.ok]
    imported = sum(r.imported for r in results)
    milestones = sum(r.milestones for r in results)
    headline = []
    if imported:
        headline.append("imported {} historic snapshot{}".format(
            imported, "" if imported == 1 else "s"))
    if milestones:
        headline.append("{} new milestone{}".format(
            milestones, "" if milestones == 1 else "s"))
    headline.extend("{}: {}".format(r.username, r.message) for r in failed[:10])
    _place_sessions(database, usernames)
    database.finish_run(run_id, ok, len(failed), "; ".join(headline))
    log.info("update run finished: %d ok, %d failed, %d imported, %d milestones",
             ok, len(failed), imported, milestones)
    return results


def update_one(client, database, username, achievements=True):
    """Ask the API to refresh one player, then store whatever we get back.

    A failed refresh still gets a GET, so a player who was updated moments ago
    (or is temporarily off the hiscores) keeps contributing current data. The
    first time we see a player, their stored history is imported too.
    """
    details = None
    message = ""
    try:
        details = client.update_player(username)
        message = "updated"
    except WomError as exc:
        message = str(exc)
        log.warning("update failed for %s: %s", username, exc)
        try:
            details = client.get_player(username)
            message = "{} (used cached profile)".format(message)
        except WomError as exc2:
            log.warning("fetch failed for %s: %s", username, exc2)
            return PlayerResult(username, False, str(exc2))

    # Wise Old Man hands back whatever capitalisation it holds, which for some
    # accounts is all lower case. The roster is where a person wrote the name
    # out, so where the two differ only in case, theirs is the one to show.
    details = _spelled_as_asked(details, username)

    try:
        player_id = database.save_player_details(details)
    except Exception as exc:
        log.exception("saving %s failed", username)
        return PlayerResult(username, False, "could not save: {}".format(exc))

    imported = 0
    if database.needs_backfill(player_id):
        imported, backfill_note = backfill_player(client, database, username, player_id)
        if backfill_note:
            message = "{}, {}".format(message, backfill_note)

    recovered = collect_recent(client, database, username, player_id)
    if recovered:
        message = "{}, {} reading{} we had missed".format(
            message, recovered, "" if recovered == 1 else "s")

    milestones = (sync_achievements(client, database, username, player_id)
                  if achievements else 0)
    if milestones:
        message = "{}, {} new milestone{}".format(
            message, milestones, "" if milestones == 1 else "s")

    return PlayerResult(username, True, message, imported, milestones)


# The furthest back a pass will re-read a player's history, and how much it
# overlaps what it already has.
#
# Normally the window is tiny: everything up to our own last reading is
# already stored, so asking from just before it returns a snapshot or two.
# Asked for a flat three hours it returned every ten-minute reading in them -
# eighteen full snapshots per player per run, nearly all of which we already
# had - and that, not the work, was what turned an eighteen second pass into
# a minute. After an outage the window widens on its own, because the last
# reading is older.
RECENT_MINUTES = 180
OVERLAP_MINUTES = 15


def _recent_since(database, player_id, now=None):
    """When to re-read a player's history from."""
    now = now or datetime.now(timezone.utc)
    floor = now - timedelta(minutes=RECENT_MINUTES)
    latest = database.latest_snapshot(player_id)
    held = parse_api_time(latest["captured_at"]) if latest else None
    if held is None:
        return floor
    return max(floor, held - timedelta(minutes=OVERLAP_MINUTES))


def collect_recent(client, database, username, player_id):
    """Fetch readings Wise Old Man took that our own polling never saw.

    `update_player` hands back the latest snapshot and nothing else, so a
    reading it made in between - most often a player's own client pushing at
    logout - is invisible to us however often we ask. It sits in the history
    endpoint, which is what this reads.

    That matters at a day boundary. Somebody who logs out at 23:55 is noticed
    by our next poll at 00:10 and their whole evening lands on the wrong day;
    the push Wise Old Man recorded at 23:55 puts it back where it happened,
    for every account, with nothing for anyone to install.

    Never allowed to break a run: the update itself is the thing worth having.
    """
    since = _recent_since(database, player_id)
    try:
        found = client.get_snapshots(username, start_date=since)
    except WomError as exc:
        log.info("could not re-read %s's recent history: %s", username, exc)
        return 0
    except Exception:
        log.exception("re-reading %s's recent history crashed", username)
        return 0
    kept = database.save_snapshots(player_id, found or [])
    if kept:
        log.info("recovered %d reading%s for %s that our polling never saw",
                 kept, "" if kept == 1 else "s", username)
    return kept


def _spelled_as_asked(details, username):
    """Settle the capitalisation of a name between the roster and the API.

    Neither is authoritative: Wise Old Man holds some names entirely in lower
    case, and a roster entry can be typed in lower case just as easily. So the
    more specific spelling wins - the one carrying more capitals - which
    upgrades either from the other and downgrades neither.

    Only where the two are the same name. A display name that has genuinely
    changed is a different string, not a differently shaped one, and that
    change has to come through.
    """
    shown = (details or {}).get("displayName") or ""
    if not shown or not username or shown == username:
        return details
    if shown.lower() != username.lower():
        return details
    if sum(c.isupper() for c in username) <= sum(c.isupper() for c in shown):
        return details
    details = dict(details)
    details["displayName"] = username
    return details


def sync_achievements(client, database, username, player_id):
    """Store any milestones Wise Old Man has recorded that we have not seen.

    The endpoint returns a player's whole achievement list, so the first call
    fills in their back catalogue and later ones only add what is new.
    """
    try:
        achievements = client.get_achievements(username)
    except WomError as exc:
        # Milestones are a bonus: never fail an update over them.
        log.warning("achievements failed for %s: %s", username, exc)
        return 0
    try:
        return database.save_achievements(player_id, achievements)
    except Exception:
        log.exception("storing achievements for %s failed", username)
        return 0


def backfill_player(client, database, username, player_id=None, force=False):
    """Import every snapshot Wise Old Man holds for a player.

    Runs once per player - on the pass that first stores them - so charts have
    real history from the start instead of building up one point at a time.
    Returns (new_snapshots, note).
    """
    if player_id is None:
        row = database.player_by_username(username)
        if row is None:
            return 0, "not stored yet"
        player_id = row["id"]
    if not force and not database.needs_backfill(player_id):
        return 0, ""

    try:
        snapshots = list(client.iter_snapshots(username))
    except WomError as exc:
        # History is a nice-to-have: a failure here must not fail the update.
        log.warning("history import failed for %s: %s", username, exc)
        return 0, "history unavailable ({})".format(exc)

    try:
        imported = database.save_snapshots(player_id, snapshots)
    except Exception as exc:
        log.exception("storing history for %s failed", username)
        return 0, "history not saved ({})".format(exc)

    database.mark_backfilled(player_id)
    log.info("imported %d/%d historic snapshots for %s", imported, len(snapshots), username)
    if not snapshots:
        return 0, "no history on record"
    note = "imported {} historic snapshot{}".format(imported, "" if imported == 1 else "s")
    if len(snapshots) >= HISTORY_LIMIT:
        # Pages arrive newest first, so the oldest end is what got left behind.
        note += " (capped at {}; older history skipped)".format(HISTORY_LIMIT)
        log.info("history for %s hit the %d snapshot cap", username, HISTORY_LIMIT)
    return imported, note
