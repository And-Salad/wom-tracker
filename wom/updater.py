"""Runs one update pass over the tracked username list.

Readings come from Jagex's hiscores (wom/hiscores.py), once an hour for
everyone and at each logout for anyone with Dink. Wise Old Man is asked only
to introduce a player we have never stored - who they are, their account
type, and the history it holds - and for its efficiency rates. It used to be
asked to update every player every ten minutes, which its documentation asks
trackers not to do and which made each run a minute and a half of waiting on
its anonymous rate limit for numbers the hiscores hand over in a second.
"""

import logging
from datetime import datetime, timedelta, timezone

from . import efficiency, hiscores, milestones, scheduler, sessions
from .api import SNAPSHOT_PAGE_SIZE, WomError
from .util import parse_api_time

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
        return "<PlayerResult {} {}>".format(self.username,
                                             "ok" if self.ok else "failed")


# How far back a run reconsiders session attribution. Long enough that a
# logout arriving late still corrects the reading it belongs to, short enough
# that this is a handful of rows a run rather than a sweep.
SESSION_LOOKBACK_DAYS = 3


def place_sessions(database, usernames):
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
               starting=None, cancelled=None, daily=False, say=None,
               thin=(), reader=None):
    """Refresh every username in turn, saving each result as it arrives.

    `client` is Wise Old Man's, for introductions, history and rates;
    `reader` is the hiscores client every reading comes from. Without one
    the readings fall back to Wise Old Man's stored profile - a read, never
    an update - which is what a caller with no hiscores client gets.

    `starting(index, total, username)` fires before each player and
    `progress(index, total, PlayerResult)` after it, so a UI can show live
    feedback across the slow bits - importing history takes several calls.
    `cancelled()` is polled to allow an early stop. `daily` also re-checks
    each account's type against the iron hiscores, which changes about as
    often as somebody dies on a hardcore. `say(text)` hears about progress
    inside one player, which is only ever a history import. `thin` names the
    accounts whose imported history is thinned as it lands - the
    celebrities. Returns the results.
    """
    usernames = list(usernames)
    thin = {name.lower() for name in thin}
    total = len(usernames)
    run_id = database.start_run(trigger, roster=total)
    results = []
    refresh_rates(client, database)

    for index, username in enumerate(usernames, start=1):
        if cancelled is not None and cancelled():
            log.info("update run cancelled after %d/%d", index - 1, total)
            break
        if starting is not None:
            try:
                starting(index, total, username)
            except Exception:
                log.exception("starting callback failed")
        result = update_one(client, database, username, say=say,
                            thin=username.lower() in thin, reader=reader,
                            daily=daily)
        results.append(result)
        if progress is not None:
            try:
                progress(index, total, result)
            except Exception:  # a UI callback must never break the run
                log.exception("progress callback failed")

    ok = sum(1 for r in results if r.ok)
    failed = [r for r in results if not r.ok]
    imported = sum(r.imported for r in results)
    found = sum(r.milestones for r in results)
    headline = []
    if imported:
        headline.append("imported {} historic snapshot{}".format(
            imported, "" if imported == 1 else "s"))
    if found:
        headline.append("{} new milestone{}".format(
            found, "" if found == 1 else "s"))
    headline.extend("{}: {}".format(r.username, r.message) for r in failed[:10])
    place_sessions(database, usernames)
    database.finish_run(run_id, ok, len(failed), "; ".join(headline))
    log.info("update run finished: %d ok, %d failed, %d imported, %d milestones",
             ok, len(failed), imported, found)
    return results


def update_one(client, database, username, say=None, thin=False, reader=None,
               daily=False):
    """Read one player's hiscores and store what they say.

    A player we have never stored is introduced through Wise Old Man first,
    because that is where their id, account type and history come from - see
    register(). After that every reading is the hiscores'.

    A hiscores failure other than "no such player" falls back to Wise Old
    Man's stored profile, so an outage at Jagex costs freshness rather than
    the reading. "No such player" does not: the account has most likely been
    renamed, and Wise Old Man's copy of the old name would read as current.
    """
    row = database.player_by_username(username)
    if row is None:
        player_id, message = register(client, database, username)
        if player_id is None:
            return PlayerResult(username, False, message)
        row = database.player_by_username(username)
        # The run asked for the types it already tracked; this one may be new.
        refresh_rates(client, database,
                      {efficiency.algorithm_type(row["type"])})
    player_id = row["id"]

    imported = 0
    message = "updated"
    if database.needs_backfill(player_id):
        imported, backfill_note = backfill_player(client, database, username,
                                                  player_id, say=say, thin=thin)
        if backfill_note:
            message = "{}, {}".format(message, backfill_note)

    found = 0
    if reader is not None:
        try:
            found = read_hiscores(reader, database, row, daily=daily)["milestones"]
        except hiscores.HiscoresNotFound:
            # Shown on the admin page beside the name, for somebody to
            # follow by hand - wom/renames.py says why it is not automatic.
            database.mark_missing(player_id)
            return PlayerResult(username, False,
                                "not on the hiscores - renamed?", imported)
        except hiscores.HiscoresError as exc:
            log.warning("hiscores failed for %s: %s", username, exc)
            failure = _from_wise_old_man(client, database, username)
            if failure is not None:
                return PlayerResult(username, False, "{}; {}".format(
                    exc, failure), imported)
            message = "{} (hiscores unavailable, used Wise Old Man's copy)".format(
                message)
    else:
        failure = _from_wise_old_man(client, database, username)
        if failure is not None:
            return PlayerResult(username, False, failure, imported)

    if found:
        message = "{}, {} new milestone{}".format(
            message, found, "" if found == 1 else "s")
    return PlayerResult(username, True, message, imported, found)


def register(client, database, username):
    """Introduce a player through Wise Old Man. Returns (player_id, note).

    Asked for what it already holds first. Only an account it has never
    seen is updated - the one POST this app still makes, once per player,
    because that is what adds them to Wise Old Man at all.
    """
    try:
        details = client.get_player(username)
    except WomError as exc:
        if exc.status != 404:
            log.warning("could not look %s up: %s", username, exc)
            return None, str(exc)
        try:
            details = client.update_player(username)
        except WomError as exc2:
            log.warning("could not register %s: %s", username, exc2)
            return None, str(exc2)
    details = _spelled_as_asked(details, username)
    try:
        player_id = database.save_player_details(details)
    except Exception as exc:
        log.exception("saving %s failed", username)
        return None, "could not save: {}".format(exc)
    # Their back catalogue of milestones, dated by Wise Old Man. Only ever
    # asked here: from now on, crossings are noticed in our own readings.
    try:
        database.save_achievements(player_id, client.get_achievements(username))
    except WomError as exc:
        log.warning("milestones unavailable for %s: %s", username, exc)
    return player_id, "registered"


def _from_wise_old_man(client, database, username):
    """Store Wise Old Man's copy of a player. Returns an error, or None.

    A read of what it already holds, never an update - the fallback for a
    run that cannot reach the hiscores.
    """
    try:
        details = client.get_player(username)
    except WomError as exc:
        log.warning("fetch failed for %s: %s", username, exc)
        return str(exc)
    try:
        database.save_player_details(_spelled_as_asked(details, username))
    except Exception as exc:
        log.exception("saving %s failed", username)
        return "could not save: {}".format(exc)
    return None


def read_hiscores(reader, database, player, daily=False, now=None):
    """Take one reading from the hiscores and store it, with what follows.

    The one path every reading takes, scheduled or prompted by a logout:
    convert to the stored shape, work out efficiency, store, then look for
    milestones crossed since the reading before. `player` is the players
    row. Raises HiscoresError (or HiscoresNotFound) when the lookup fails.

    Returns {"moved": whether any number changed, "milestones": how many
    were new}. A logout's reading is retried when nothing moved, because the
    hiscores can lag the logout screen by a moment.
    """
    username = player["username"]
    asked = player["display_name"] or username
    payload = reader.fetch(asked)
    stamp = _stamp(now or datetime.now(timezone.utc))
    snap = hiscores.to_snapshot(payload, stamp)
    exp = hiscores.overall(payload)

    player_type = player["type"]
    if daily and exp is not None:
        try:
            checked = reader.account_type(asked, exp)
            if checked != player_type:
                log.info("%s is now %s (was %s)", username, checked,
                         player_type)
                database.set_player_type(player["id"], checked)
                player_type = checked
        except hiscores.HiscoresError as exc:
            log.info("could not check %s's account type: %s", username, exc)

    algorithm = efficiency.algorithm_for(database, player_type)
    ehp, ehb, ttm = efficiency.annotate(snap, algorithm)

    before = database.values_at(player["id"])
    latest = database.latest_snapshot(player["id"])
    after = milestones.values_of(snap)
    moved = any(before.get(metric) != value for metric, value in after.items()
                if value is not None and value >= 0)

    database.save_hiscores_reading(
        player["id"], snap,
        combat_level=hiscores.combat_level(hiscores.levels(snap)),
        exp=exp, ehp=ehp, ehb=ehb, ttm=ttm)

    found = 0
    crossed = milestones.crossed(before, after,
                                 latest["captured_at"] if latest else None,
                                 stamp)
    if crossed:
        found = database.save_achievements(player["id"], crossed)
    return {"moved": moved, "milestones": found}


def _stamp(moment):
    """A UTC moment written the way every stored reading is."""
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


# How old the stored efficiency rates may get before a run asks again.
RATES_MAX_AGE = timedelta(hours=20)


def refresh_rates(client, database, types=None, now=None):
    """Re-read Wise Old Man's efficiency rates for every type we track.

    At most daily, and never fatal: rates change with game updates, a few
    times a year, so yesterday's are as good as today's for any run that
    cannot reach them.
    """
    now = now or datetime.now(timezone.utc)
    if types is None:
        types = {efficiency.algorithm_type(p["type"])
                 for p in database.players()} or {"main"}
    for kind in sorted(types):
        held = parse_api_time(database.efficiency_rates(kind)[2])
        if held is not None and now - held < RATES_MAX_AGE:
            continue
        try:
            for metric in ("ehp", "ehb"):
                database.save_efficiency_rates(
                    kind, metric, client.get_efficiency_rates(kind, metric))
            log.info("refreshed %s efficiency rates", kind)
        except WomError as exc:
            log.warning("could not refresh %s efficiency rates: %s", kind, exc)


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


# How many pages of history one update pass imports for a friend. Theirs is
# a page or two, so this is a ceiling rather than a pace: it keeps an account
# with an unexpectedly long history from holding up everyone else's update,
# and the import carries on next run. A celebrity is not held to it - see
# backfill_player.
BACKFILL_PAGES_PER_PASS = 10


def backfill_player(client, database, username, player_id=None, force=False,
                    say=None, thin=False, pages=BACKFILL_PAGES_PER_PASS):
    """Import the snapshots Wise Old Man holds for a player, a stretch a run.

    Walks back from where the last pass stopped (`backfill_before`) for up to
    `pages` pages, and marks the player done only when there is nothing older.
    So charts gain real history over a few runs rather than one point at a
    time, and no account's history is too long to have all of it.

    `force` starts again from now and does not stop until the end: it is the
    admin's re-import, asked for on purpose and run as a job of its own.

    Neither does a celebrity's (`thin`). Theirs is the long one - a hundred
    and fifty pages is eight minutes at the anonymous rate, longer than a
    slot, so the pass it lands in runs over and the slot after it is skipped.
    Friends are updated first in every pass, so none of theirs waits on it.
    That is a one-off, and it was chosen over having their history trickle in
    ten pages a run for most of a day.

    Stored a page at a time as the pages arrive, and each page oldest first.
    The API pages newest first, and stored in that order no reading had one
    before it to be compared with, so every metric was written for every
    snapshot. Within a page each now follows the one before it; only the
    oldest of each page starts from nothing, which is once per two hundred.

    `thin` is for celebrities: each page is thinned as it lands, to one
    reading a day past the recent month, the same as the nightly compaction
    does to them. Otherwise an import stores thirty thousand readings that
    compaction then removes one night later - see compact_snapshots.

    `say(text)` is told after each page, because from the admin page a
    silent import could not be told apart from a hung one.

    Returns (new_snapshots, note).
    """
    if player_id is None:
        row = database.player_by_username(username)
        if row is None:
            return 0, "not stored yet"
        player_id = row["id"]
    if not force and not database.needs_backfill(player_id):
        return 0, ""

    before = None if force else database.backfill_before(player_id)
    limit = None if force or thin else pages
    imported = seen = 0
    finished = True
    try:
        for page, batch in enumerate(client.iter_snapshots_before(
                username, before=before, max_pages=limit), start=1):
            try:
                imported += database.save_snapshots(player_id,
                                                    list(reversed(batch)))
                if thin:
                    # The default window, the one the nightly pass keeps.
                    database.compact_snapshots(thin=[player_id],
                                               only=[player_id], vacuum=False,
                                               repeats=False)
            except Exception as exc:
                log.exception("storing history for %s failed", username)
                return imported, "history not saved ({})".format(exc)
            oldest = min(s["createdAt"] for s in batch)
            finished = len(batch) < SNAPSHOT_PAGE_SIZE
            if before is not None and oldest >= before:
                # Only the reading the last page ended on, which the API's
                # inclusive end date hands back: nothing older, so done.
                finished = True
                continue
            seen += len(batch)
            before = oldest
            # Moved only once the page is safely stored, so a pass that fails
            # part way resumes from the last page it kept.
            database.set_backfill_before(player_id, oldest)
            log.info("history for %s: page %d, %d snapshots, back to %s",
                     username, page, seen, oldest[:10])
            if say is not None:
                try:
                    say("{}: importing history, back to {}".format(
                        username, oldest[:10]))
                except Exception:
                    log.exception("progress callback failed")
    except WomError as exc:
        # History is a nice-to-have: a failure here must not fail the update.
        # Whatever pages did arrive are kept, and the import is not marked
        # done, so the next pass carries on from the last page it stored.
        log.warning("history import failed for %s: %s", username, exc)
        return imported, "history unavailable ({})".format(exc)

    if finished:
        database.mark_backfilled(player_id)
    log.info("imported %d/%d historic snapshots for %s%s", imported, seen,
             username, "" if finished else ", more next run")
    if not seen:
        return 0, "no history on record"
    note = "imported {} historic snapshot{}".format(
        imported, "" if imported == 1 else "s")
    if not finished:
        note += ", back to {} so far".format(before[:10])
    return imported, note
