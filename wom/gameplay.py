"""What a player reports while they are playing, and what we do with it.

Dink's session events say when someone started and stopped. These say what
happened in between: a collection log slot filled, a level gained, a boss
count passed. They are opt-in per player - a second URL and a few toggles -
so this is sparse, and code that reads it has to cope with having nothing.

Two things are done with each one. It is stored whole, because the detail is
the point and none of it fits the metrics table: which item, from which drop,
at which rank. And where the payload happens to *be* a metric we already
track, the value is written at the moment it happened, so the charts stop
rounding that moment to the next ten minute poll.

Levels are stored but not written through. Our level total lives in the
`level` column of the `overall` row, beside overall experience in `value`,
and a level reported without experience would be a row that reads as
authoritative while carrying half an answer. Worth doing later, deliberately,
rather than as a side effect of this.
"""

import logging
import re

log = logging.getLogger(__name__)

# Dink's notification types, and what we call them.
KINDS = {
    "COLLECTION": "collection",
    "LEVEL": "level",
    "KILL_COUNT": "kill_count",
}

# Where a collection log count lives in our metrics.
COLLECTION_METRIC = "collections_logged"


def slug(name):
    """A Wise Old Man metric name from a display name.

    Apostrophes vanish rather than becoming separators - Wise Old Man writes
    Kree'arra as `kreearra` and Vet'ion as `vetion`, so replacing them the way
    spaces are replaced misses both.
    """
    text = re.sub(r"[’']", "", (name or "").lower())
    return re.sub(r"[^a-z0-9]+", "_", text).strip("_")


def boss_metric(database, name):
    """The boss metric this name refers to, or None if we do not track it.

    Checked against what we already store rather than trusted, because the
    name arrives from a plugin and a wrong guess would invent a boss. The
    `the_` variants are tried both ways: Wise Old Man keeps the article for
    `the_whisperer` and drops it for `nightmare`, and there is no rule in it.
    """
    base = slug(name)
    if not base:
        return None
    for candidate in (base, base[4:] if base.startswith("the_") else "the_" + base):
        if candidate and database.knows_metric("boss", candidate):
            return candidate
    return None


def extract(kind, body):
    """The rows this event becomes, as [(subject, quantity)].

    A level-up can carry several skills at once, which is why this returns a
    list rather than one pair - three skills levelling in one tick is three
    things that happened, not one.
    """
    extra = body.get("extra")
    extra = extra if isinstance(extra, dict) else {}
    if kind == "collection":
        return [(extra.get("itemName"), _number(extra.get("completedEntries")))]
    if kind == "kill_count":
        return [(extra.get("boss"), _number(extra.get("count")))]
    if kind == "level":
        levelled = extra.get("levelledSkills")
        if not isinstance(levelled, dict):
            return []
        return [(name, _number(value)) for name, value in sorted(levelled.items())]
    return []


def store(database, username, kind, happened_at, body):
    """Record one event and, where we can, the metric it reports.

    Returns how many rows were written, counting both.
    """
    written = 0
    player = database.player_by_username(username)
    for subject, quantity in extract(kind, body):
        if database.record_game_event(username, kind, happened_at, body,
                                      subject=subject, quantity=quantity):
            written += 1
        if player is None or quantity is None:
            continue
        written += _as_metric(database, player, kind, subject, quantity,
                              happened_at)
    return written


def _as_metric(database, player, kind, subject, quantity, happened_at):
    """Write the reading this event amounts to, if it amounts to one."""
    if kind == "collection":
        rows = [("activity", COLLECTION_METRIC, quantity)]
    elif kind == "kill_count":
        metric = boss_metric(database, subject)
        if metric is None:
            log.info("gameplay: no boss metric for %r, keeping the event only",
                     subject)
            return 0
        rows = [("boss", metric, quantity)]
    else:
        return 0
    return database.record_derived_state(player["id"], happened_at, rows,
                                         origin="reported")


def _number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
