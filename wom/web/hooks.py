"""The one place on this app anything may write to without a password.

Dink is a RuneLite plugin. Point its "Custom Metadata Handler" setting at a
URL and it POSTs a JSON body at both ends of a session: a login six seconds
after the client finishes logging in, carrying that account's own reading of
its experience, and a logout, which carries only the fact and the moment.

Between them that is a session, measured. Wise Old Man can infer an ending
from the hiscores moving and cannot see a beginning at all, so a three hour
session reaches us as a single jump and lands in whichever ten minute window
we happened to notice it in. The logout is also better than what we infer:
it is the moment itself rather than a ten minute bracket around it, and it
arrives however little was gained, where the hiscore route stays silent
under ten thousand experience.

Nothing here changes a number anyone sees. It records logins and stops; what
to do with them is a decision to make against real data rather than in
advance.

The plugin cannot send a header, so the URL is the entire credential - the
same bargain Discord's own webhook URLs make. Every player therefore gets
their own: it identifies the sender without trusting the name in the body,
and one that leaks is revoked on its own.
"""

import hmac
import json
import logging

from flask import Blueprint, Response, current_app, request

from ..config import Config

log = logging.getLogger(__name__)

hooks = Blueprint("hooks", __name__)

# Dink's login body is a few kilobytes - every skill twice over, the
# collection log and quest counts, and a list of pets. Ten times that leaves
# room for a bigger account than any of ours and still refuses anything that
# could only be someone using us as free storage.
MAX_BODY = 64 * 1024

# Both ends of a session, as Dink names them. A LOGIN carries the account's
# live experience; a LOGOUT carries only the fact and the moment, and unlike
# the hiscores it has no threshold under which it stays quiet.
#
# The metadata webhook receives more than these: group ironman bank contents
# when the storage screen opens, and so on. Those are accepted and dropped -
# refusing them would only make the plugin retry.
KINDS = {"LOGIN": "login", "LOGOUT": "logout"}

# On by default in Dink, and none of our business: who someone is on Discord,
# and which clan they are in. Dropped as the body is read rather than stored
# and ignored, so they are never written down at all.
UNWANTED = ("discordUser", "clanName", "groupIronClanName", "dinkAccountHash")


@hooks.route("/hook/dink/<token>", methods=["POST"])
def dink(token):
    """Accept one metadata push from Dink.

    Deliberately outside the tripwire. The tripwire exists to stop serving
    data that costs us something to produce; this endpoint produces nothing
    and costs a row. Behind it, a burst of dashboard traffic would silently
    cost us every session boundary until someone noticed - and those cannot
    be fetched again later, unlike anything the tripwire protects.
    """
    username = _owner(token)
    # Logged before anything can refuse it. Without this line a request that
    # arrived and was rejected looks exactly like one that never arrived,
    # which is the first question asked when a player says it is not working.
    # The token is described, never written down.
    log.info("dink: %s bytes of %s, %d-character token, %s", request.content_length,
             request.content_type or "no content type", len(token or ""),
             "known" if username else "no match")
    if username is None:
        # No hint that the path shape was right, or that another token exists.
        return Response("Not found.", status=404, mimetype="text/plain")

    limits = current_app.config["LIMITS"]
    if limits.dink_per_token.take(username):
        log.warning("dink: refusing a burst from %s", username)
        return Response("Too many requests.", status=429, mimetype="text/plain")

    length = request.content_length
    if length is None or length > MAX_BODY:
        return Response("Body too large.", status=413, mimetype="text/plain")

    body = _body()
    if not isinstance(body, dict):
        log.warning("dink: unreadable body from %s", username)
        return Response("Expected a JSON object.", status=400,
                        mimetype="text/plain")

    kind = KINDS.get(body.get("type"))
    if kind is None:
        # Accepted, so it is not retried, and forgotten.
        log.info("dink: %s sent a %s, which we do not keep", username,
                 body.get("type"))
        return Response(status=204)

    kept = {key: value for key, value in body.items() if key not in UNWANTED}
    extra = kept.get("extra")
    reading = _reading(extra if isinstance(extra, dict) else {})
    database = current_app.config["DATABASE"]
    row_id = database.record_session_event(username, kind, reading, kept)
    if row_id is None:
        log.info("dink: repeat %s from %s, ignored", kind, username)
    else:
        log.info("dink: %s %s (world %s, %s xp)", username, kind,
                 reading.get("world"), reading.get("total_exp"))
    return Response(status=204)


def _owner(token):
    """The player a token belongs to, or None.

    Compared in constant time against every issued token, so a caller cannot
    learn a token a character at a time from how long the answer took.
    """
    given = (token or "").strip()
    # compare_digest refuses a str with anything outside ASCII, and the route
    # will hand us whatever was typed. Nothing we issue is non-ASCII, so this
    # is a miss rather than a 500.
    if not given or not given.isascii():
        return None
    tokens = Config().get("dink_tokens") or {}
    found = None
    for username, secret in tokens.items():
        if secret and hmac.compare_digest(str(secret), given):
            found = username
    return found


def _body():
    """The JSON Dink sent, whichever way it sent it.

    Login metadata has no screenshot attached, so it arrives as a plain JSON
    body. The multipart form is what the plugin uses when there *is* an image,
    and it is read here too rather than left as a shape that silently 400s if
    Dink ever attaches one.
    """
    if request.mimetype == "multipart/form-data":
        raw = request.form.get("payload_json")
        if not raw:
            return None
        try:
            return json.loads(raw)
        except ValueError:
            return None
    return request.get_json(silent=True)


def _reading(extra):
    """The few numbers worth their own columns. The rest stays in the payload."""
    skills = extra.get("skills") if isinstance(extra.get("skills"), dict) else {}
    clog = extra.get("collectionLog")
    return {
        "world": _int(extra.get("world")),
        "total_exp": _float(skills.get("totalExperience")),
        "total_level": _int(skills.get("totalLevel")),
        "collections": _int(clog.get("completed")) if isinstance(clog, dict) else None,
    }


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
