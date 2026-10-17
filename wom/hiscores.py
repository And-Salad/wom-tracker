"""Client for Jagex's own Old School hiscores, and the shape we store them in.

This is where the readings come from. Wise Old Man reads the same hiscores
when it is asked to update a player, so asking it was a second hop to the
same numbers - and asking every ten minutes, for every player, is what its
documentation asks trackers not to do. Compared side by side on 2026-10-17,
the two agreed on every value they share for every account we track.

The endpoint is undocumented and has no published rate limit; it sits behind
Cloudflare. A request a second is far below anything it notices, and a run
here is one request a player an hour plus one at each logout.

The JSON form is used rather than the older CSV one because it names every
row. The CSV is positional, so each boss Jagex adds shifts every row after
it and a parser that trusts the order quietly files kills under the wrong
boss until somebody notices.
"""

import logging
import threading
import time

import requests

from .gameplay import slug

log = logging.getLogger(__name__)

BASE_URL = "https://secure.runescape.com/m={table}/index_lite.json"

# The regular table lists every account, ironmen included; the others list
# only their own. Asked in this order, the first that knows a player at the
# same experience names their account type - an ultimate is on all three
# iron tables, a hardcore on two.
TYPE_TABLES = (
    ("ultimate", "hiscore_oldschool_ultimate"),
    ("hardcore", "hiscore_oldschool_hardcore_ironman"),
    ("ironman", "hiscore_oldschool_ironman"),
)
REGULAR_TABLE = "hiscore_oldschool"

# Spacing between requests. Shared by everything in the process that asks,
# because the scheduler and the logout worker run at the same time.
MIN_INTERVAL = 1.0

# Jagex's name -> ours, where slugging the name does not already give it.
# Ours are Wise Old Man's, which every stored row and chart already uses.
RENAMED = {
    "runecraft": "runecrafting",
    "lms_rank": "last_man_standing",
    "pvp_arena_rank": "pvp_arena",
    "rifts_closed": "guardians_of_the_rift",
    "tombs_of_amascut_expert_mode": "tombs_of_amascut_expert",
}

# The hiscore rows that are not bosses. Everything else on the activities
# list is, so a boss Jagex adds is filed - under its own slug - without a
# code change.
ACTIVITIES = {
    "bounty_hunter_hunter", "bounty_hunter_rogue",
    "clue_scrolls_all", "clue_scrolls_beginner", "clue_scrolls_easy",
    "clue_scrolls_medium", "clue_scrolls_hard", "clue_scrolls_elite",
    "clue_scrolls_master",
    "last_man_standing", "pvp_arena", "soul_wars_zeal",
    "guardians_of_the_rift", "colosseum_glory", "collections_logged",
}

# Rows nobody here tracks: seasonal points, and the retired Bounty Hunter
# scores, which have not moved since 2023.
IGNORED = {
    "grid_points", "league_points", "deadman_points",
    "bounty_hunter_legacy_hunter", "bounty_hunter_legacy_rogue",
}

class HiscoresError(Exception):
    """A lookup failed. `status` is the HTTP code, or None for transport errors."""

    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


class HiscoresNotFound(HiscoresError):
    """The hiscores have never heard of this name - renamed, or never ranked."""


class HiscoresClient:
    def __init__(self, timeout=20, session=None, interval=MIN_INTERVAL):
        self.timeout = timeout
        self.session = session or requests.Session()
        self.interval = interval
        self._lock = threading.Lock()
        self._next_allowed = 0.0

    def _throttle(self):
        with self._lock:
            wait = self._next_allowed - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self._next_allowed = time.monotonic() + self.interval

    def fetch(self, username, table=REGULAR_TABLE, attempts=3):
        """One player's row from one hiscore table, as Jagex sends it."""
        url = BASE_URL.format(table=table)
        params = {"player": str(username).strip()}
        headers = {"User-Agent": "WOM-Tracker/1.0", "Accept": "application/json"}
        last = None
        for attempt in range(attempts):
            self._throttle()
            try:
                resp = self.session.get(url, params=params, headers=headers,
                                        timeout=self.timeout)
            except requests.RequestException as exc:
                last = HiscoresError("network error: {}".format(exc))
                time.sleep(2 ** attempt)
                continue
            if resp.status_code == 404:
                raise HiscoresNotFound(
                    "{} is not on the hiscores".format(username), 404)
            if resp.status_code == 429 or resp.status_code >= 500:
                retry = resp.headers.get("Retry-After") or ""
                delay = float(retry) if retry.isdigit() else 2 ** (attempt + 1)
                last = HiscoresError("the hiscores answered {}".format(
                    resp.status_code), resp.status_code)
                time.sleep(delay)
                continue
            if resp.status_code >= 400:
                raise HiscoresError("the hiscores answered {}".format(
                    resp.status_code), resp.status_code)
            # Cloudflare answers a challenge page with a 200 and HTML, so a
            # body that is not the JSON we asked for is a refusal, not data.
            try:
                payload = resp.json()
            except ValueError as exc:
                raise HiscoresError("the hiscores sent something that is not "
                                    "JSON - likely a Cloudflare challenge",
                                    resp.status_code) from exc
            if not isinstance(payload, dict) or "skills" not in payload:
                raise HiscoresError("the hiscores sent an unexpected shape",
                                    resp.status_code)
            return payload
        raise last or HiscoresError("hiscores lookup failed")

    def account_type(self, username, overall_exp):
        """The account's type, read from which iron tables list it.

        A table is only believed when it shows the same overall experience as
        the regular one. A de-ironed or dead hardcore account keeps its old
        row on the iron table, frozen where it left - so being listed is not
        enough, being listed *current* is.
        """
        for name, table in TYPE_TABLES:
            try:
                row = self.fetch(username, table=table)
            except HiscoresNotFound:
                continue
            if overall(row) == overall_exp:
                return name
        return "regular"


def metric_name(jagex_name):
    """Our metric name for a hiscore row's name."""
    key = slug(jagex_name)
    return RENAMED.get(key, key)


def overall(payload):
    """Overall experience from a hiscore payload, or None."""
    for row in payload.get("skills") or ():
        if slug(row.get("name")) == "overall":
            return row.get("xp")
    return None


def to_snapshot(payload, captured_at):
    """A hiscore payload in the shape save_snapshot stores.

    That shape is Wise Old Man's - `{"createdAt", "data": {"skills",
    "bosses", "activities"}}` - because every reading we already hold is in
    it, and a second shape would mean a second reader of everything.

    Unranked rows are passed through as they come. Jagex's JSON sends the
    real count even below the hiscore threshold - one kill of Artio is rank
    -1, score 1 - and that is exactly what Wise Old Man stored, so a reading
    from here and one from there agree row for row (tests/test_hiscores.py).
    """
    skills, bosses, activities = {}, {}, {}
    for row in payload.get("skills") or ():
        name = metric_name(row.get("name"))
        skills[name] = {"metric": name, "rank": row.get("rank"),
                        "level": row.get("level"), "experience": row.get("xp")}
    for row in payload.get("activities") or ():
        name = metric_name(row.get("name"))
        if name in IGNORED:
            continue
        value = row.get("score")
        if name in ACTIVITIES:
            activities[name] = {"metric": name, "rank": row.get("rank"),
                                "score": value}
        elif name:
            bosses[name] = {"metric": name, "rank": row.get("rank"),
                            "kills": value}
    return {"createdAt": captured_at,
            "data": {"skills": skills, "bosses": bosses,
                     "activities": activities}}


def levels(snapshot):
    """{skill: level} from a snapshot made by to_snapshot."""
    return {name: (entry.get("level") or 1)
            for name, entry in snapshot["data"]["skills"].items()}


def combat_level(skill_levels):
    """The in-game combat level, by the game's own formula."""
    def lvl(name, floor=1):
        return max(floor, int(skill_levels.get(name) or floor))

    base = 0.25 * (lvl("defence") + lvl("hitpoints", 10) + lvl("prayer") // 2)
    melee = 0.325 * (lvl("attack") + lvl("strength"))
    ranged = 0.325 * ((3 * lvl("ranged")) // 2)
    magic = 0.325 * ((3 * lvl("magic")) // 2)
    return int(base + max(melee, ranged, magic))


_shared = None
_shared_lock = threading.Lock()


def shared():
    """The process's one client, so everything asking shares its pacing."""
    global _shared
    with _shared_lock:
        if _shared is None:
            _shared = HiscoresClient()
        return _shared
