"""The admin side of the dashboard: everything that changes state.

The public pages stay read-only and unauthenticated - that is the whole point
of the share link. Everything here changes state, so it all sits behind a
password. Fail closed: with no password configured the admin routes do not
merely reject requests, they are never registered, so a deployment that forgets
to set one is not quietly wide open.
"""

import hmac
import logging
import os
import secrets
import time
from datetime import datetime, timedelta, timezone
from functools import wraps

from flask import (
    Blueprint,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from .. import periods, scheduler
from .. import summaries as core
from ..api import WomClient
from ..colors import normalise, player_color, set_player_color
from ..config import ENV_KEYS, Config, normalise_usernames
from ..sessions import MAX_SESSION_HOURS
from ..summaries import SUMMARY_EFFORTS, SUMMARY_MODELS
from ..updater import backfill_player, update_all
from ..util import fmt_ago, fmt_datetime, fmt_int, parse_api_time
from .hooks import public_url
from .limits import client_address

log = logging.getLogger(__name__)

admin = Blueprint("admin", __name__)

PASSWORD_ENV = "WOM_ADMIN_PASSWORD"

# What a wrong guess costs before the answer comes back. Small enough not to
# be noticed by someone who mistyped, large enough that guessing at speed is
# not worth attempting. A name rather than a literal so the tests can set it
# to zero: at half a second a guess they were spending a third of the suite's
# runtime asleep, which is a real cost for no extra coverage.
WRONG_PASSWORD_DELAY = 0.5


# Enough to cover a group without making the box a menu: anything else can be
# typed, and is checked before it is stored.
COMMON_ZONES = (
    "America/New_York", "America/Chicago", "America/Denver",
    "America/Los_Angeles", "America/Sao_Paulo", "Europe/London",
    "Europe/Dublin", "Europe/Paris", "Europe/Berlin", "Europe/Helsinki",
    "Africa/Johannesburg", "Asia/Dubai", "Asia/Kolkata", "Asia/Singapore",
    "Asia/Tokyo", "Australia/Perth", "Australia/Sydney", "Pacific/Auckland",
    "UTC",
)


def _is_a_zone(name):
    """True for a name zoneinfo can resolve on this machine."""
    try:
        from zoneinfo import ZoneInfo
        ZoneInfo(name)
        return True
    except Exception:
        return False


def admin_password():
    value = os.environ.get(PASSWORD_ENV) or ""
    return value.strip()


def admin_enabled():
    return bool(admin_password())


def signed_in():
    return bool(session.get("wom_admin"))


def requires_login(view):
    @wraps(view)
    def guarded(*args, **kwargs):
        if not signed_in():
            return redirect(url_for("admin.login", next=request.path))
        return view(*args, **kwargs)
    return guarded


# -- signing in -----------------------------------------------------------

@admin.route("/admin/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        address, source = client_address()
        sign_in = current_app.config["LIMITS"].sign_in
        # take(), not check() then record(): six guesses is small enough that
        # the gap between the two is a real fraction of it, and eight server
        # threads posting at once could all pass a check only one should.
        # Every attempt reserves a slot; a correct one hands it straight back.
        waiting = sign_in.take(address)
        if waiting:
            error = "Too many attempts. Try again in {} seconds.".format(waiting)
        elif hmac.compare_digest(request.form.get("password", ""), admin_password()):
            sign_in.refund(address)
            session["wom_admin"] = True
            session.permanent = True
            return redirect(request.args.get("next") or url_for("admin.settings"))
        else:
            # A wrong guess should cost real time even before the lockout.
            time.sleep(WRONG_PASSWORD_DELAY)
            error = "That is not the password."
            log.warning("failed admin sign-in from %s via %s", address, source)
    return render_template("admin_login.html", error=error, page="admin")


@admin.route("/admin/logout", methods=["POST"])
def logout():
    session.pop("wom_admin", None)
    return redirect(url_for("pages.dashboard"))


# -- settings -------------------------------------------------------------

@admin.route("/admin", methods=["GET"])
@requires_login
def settings():
    config = Config()
    database = current_app.config["DATABASE"]
    players = database.players()
    known = {p["username"]: p for p in players}
    tokens = config.get("dink_tokens") or {}
    roster = []
    for index, name in enumerate(config.get("usernames", [])):
        row = known.get(name.lower())
        seen = database.last_session_event(name.lower())
        roster.append({
            "username": name,
            "display_name": row["display_name"] if row is not None else name,
            "color": player_color(config, name, index),
            "snapshots": database.snapshot_count(row["id"]) if row is not None else 0,
            "updated": row["updated_at"] if row is not None else None,
            "token": tokens.get(name.lower(), ""),
            "url": public_url(tokens[name.lower()])
                   if tokens.get(name.lower()) else "",
            "events": database.session_event_count(name.lower()),
            "reported": database.game_event_count(name.lower()),
            "state": session_state(seen),
            "last_seen": (fmt_datetime(seen["happened_at"])
                          if seen is not None else None),
            "last_ago": fmt_ago(seen["happened_at"]) if seen is not None else "",
            "last_exp": fmt_int(seen["total_exp"], dash="") if seen is not None else "",
        })
    tripwire = current_app.config["LIMITS"].api_tripwire
    return render_template(
        "admin.html", page="admin", config=config, roster=roster,
        models=SUMMARY_MODELS, efforts=SUMMARY_EFFORTS,
        env_keys=ENV_KEYS, zones=COMMON_ZONES,
        job=current_app.config["JOBS"].status(),
        tripwire=tripwire.status() if tripwire else None,
        periods=[p.key for p in periods.PERIODS])


@admin.route("/admin/settings", methods=["POST"])
@requires_login
def save_settings():
    config = Config()
    names = normalise_usernames(request.form.get("usernames", "").splitlines())
    config["usernames"] = names
    config["summaries_enabled"] = bool(request.form.get("summaries_enabled"))
    # Only the models the page offers: anything else is stored happily and
    # then fails on every future API call, visible only in the log.
    model = request.form.get("summary_model", "")
    config["summary_model"] = model if model in SUMMARY_MODELS else "claude-sonnet-5"
    effort = request.form.get("summary_effort", "")
    config["summary_effort"] = effort if effort in SUMMARY_EFFORTS else "low"
    config["user_agent_contact"] = request.form.get("user_agent_contact", "").strip()
    # A zone this machine cannot resolve would move every day boundary to UTC
    # without saying so, which is a strange way to find out you typed it wrong.
    asked = request.form.get("timezone", "").strip()
    if asked and asked != config.get("timezone"):
        if _is_a_zone(asked):
            config["timezone"] = asked
            scheduler.forget_zone()
            flash("Days now run midnight to midnight in {}.".format(asked))
        else:
            flash("{} is not a time zone this machine knows. Use a name like "
                  "Europe/London.".format(asked))
    # A key supplied by the environment is not editable here, and a blank box
    # means "leave it alone" rather than "erase it" -- a password box that
    # cannot show what it holds cannot be emptied on purpose either, so the
    # tick beside it is the only way to say "erase it".
    for key in ("api_key", "anthropic_api_key"):
        if config.is_from_env(key):
            continue
        if request.form.get("clear_" + key):
            config[key] = ""
            continue
        given = request.form.get(key, "").strip()
        if not given:
            continue
        # The one value that is certainly not an API key. Browsers ignore
        # autocomplete="off" on a password field by design, so a password
        # manager fills the admin password into these boxes, and the next
        # save stored it as a key - which the Wise Old Man API then answers
        # 403 to on every request, looking for all the world like the key was
        # never cleared. The markup asks not to be filled; this is what
        # happens when something does it anyway.
        if hmac.compare_digest(given, admin_password()):
            flash("That is this dashboard's own password, not an API key - "
                  "your browser most likely filled it in. Nothing was stored.")
            continue
        config[key] = given
    config.save()
    flash("Settings saved. {} player{} tracked.".format(
        len(names), "" if len(names) == 1 else "s"))
    return redirect(url_for("admin.settings"))


@admin.route("/admin/colour", methods=["POST"])
@requires_login
def save_colour():
    config = Config()
    username = request.form.get("username", "")
    colour = normalise(request.form.get("colour", ""))
    if not username or colour is None:
        flash("That is not a colour.")
    else:
        set_player_color(config, username, colour)
        flash("Recoloured {}.".format(username))
    return redirect(url_for("admin.settings"))


def session_state(event, now=None):
    """Whether a player is in game, as far as the plugin has told us.

    Inferred from the last event and nothing else, so it is only ever as good
    as the last thing Dink managed to send. A client that crashed sent no
    logout, and this would otherwise show that account as playing forever -
    hence the age check, using the same ceiling sessions.py puts on a login
    it never saw closed.
    """
    if event is None:
        return {"label": "", "world": None, "note": ""}
    when = parse_api_time(event["happened_at"])
    old = when is None or (now or datetime.now(timezone.utc)) - when > timedelta(
        hours=MAX_SESSION_HOURS)
    if event["kind"] != "login":
        return {"label": "logged out", "world": None, "note": ""}
    if old:
        return {"label": "logged in", "world": event["world"],
                "note": "no logout since"}
    return {"label": "in game", "world": event["world"], "note": ""}


@admin.route("/admin/dink", methods=["POST"])
@requires_login
def dink():
    """Issue or revoke one player's webhook URL.

    A token is the whole credential, so issuing again replaces the old one
    rather than adding to it: there is never more than one live URL per
    player, and handing out a new one is how you retire a leaked one.
    """
    config = Config()
    username = " ".join(request.form.get("username", "").split()).lower()
    action = request.form.get("action", "")
    if not username or username not in [
            n.lower() for n in config.get("usernames", [])]:
        flash("That is not a tracked player.")
        return redirect(url_for("admin.settings"))

    tokens = dict(config.get("dink_tokens") or {})
    if action == "revoke":
        if tokens.pop(username, None) is None:
            flash("{} had no webhook URL.".format(username))
        else:
            log.warning("admin revoked the Dink webhook for %s", username)
            flash("Revoked {}. Their old URL now answers 404.".format(username))
    else:
        replaced = username in tokens
        tokens[username] = secrets.token_urlsafe(24)
        log.info("admin issued a Dink webhook for %s", username)
        flash("{} a URL for {}.".format(
            "Replaced" if replaced else "Issued", username))
    config["dink_tokens"] = tokens
    config.save()
    return redirect(url_for("admin.settings"))


@admin.route("/admin/prune", methods=["POST"])
@requires_login
def prune():
    config = Config()
    database = current_app.config["DATABASE"]
    removed = database.prune_players(config.get("usernames", []))
    flash("Removed {} player{} no longer on the list.".format(
        removed, "" if removed == 1 else "s"))
    return redirect(url_for("admin.settings"))


# -- prompts --------------------------------------------------------------

@admin.route("/admin/resume", methods=["POST"])
@requires_login
def resume():
    """Clear the tripwire and serve data again."""
    tripwire = current_app.config["LIMITS"].api_tripwire
    if not tripwire.tripped:
        flash("Nothing to resume - the data endpoints are already serving.")
    else:
        was = tripwire.tripped_by
        tripwire.reset()
        log.warning("admin resumed the data endpoints (tripped by %s)", was)
        flash("Serving again. It was tripped by {}.".format(was))
    return redirect(url_for("admin.settings"))


KINDS = (("player", "Per-player"), ("group", "Group round-up"))


def _windows_for(kind):
    """Which windows a prompt of this kind is ever asked for.

    A player's notes cover all five; a leaderboard's round-ups cover the
    three windows it has something to say about. Offering a group prompt for
    a quarter would create a file nothing ever loads.
    """
    return (periods.SUMMARY_PERIODS if kind == "player"
            else periods.GROUP_PERIODS)


def _prompt_rows(config):
    """Every prompt file there is to edit: the two bases, then any override.

    A period-specific file wins over the base for that period, and dropping
    one in is the supported way to say something different in a yearly note
    than in a daily one. They were editable only over SSH, which meant the
    prompts actually driving the quarterly and yearly round-ups could not be
    read - never mind changed - from the page whose whole job is the prompts.
    """
    rows = []
    for kind, label in KINDS:
        rows.append({"kind": kind, "period": "", "title": label + " prompt",
                     "text": core.load_prompt(config, None, kind=kind),
                     "override": False})
        for key in _windows_for(kind):
            path = core.period_prompt_path(key, kind=kind)
            if not os.path.exists(path):
                continue
            with open(path, encoding="utf-8") as handle:
                rows.append({"kind": kind, "period": key,
                             "title": "{} prompt for {}".format(label, key),
                             "text": handle.read().strip(), "override": True})
    return rows


def _seed_override(config, choice):
    """Create a period's own prompt file, copied from the base it overrides.

    Seeded rather than blank: an override is nearly always the base prompt
    with a paragraph changed, and starting from an empty box invites losing
    the instructions that make the digest readable.
    """
    kind, _, period = choice.partition(":")
    if kind not in ("player", "group") or period not in _windows_for(kind):
        flash("That is not a prompt to add.")
        return redirect(url_for("admin.prompts"))
    path = core.period_prompt_path(period, kind=kind)
    if os.path.exists(path):
        flash("There is already a {} prompt for {}.".format(kind, period))
        return redirect(url_for("admin.prompts"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(core.load_prompt(config, None, kind=kind).strip() + "\n")
    flash("Added a {} prompt for {}, copied from the base one.".format(
        kind, period))
    return redirect(url_for("admin.prompts"))


def _missing_overrides(config):
    """The (kind, period) pairs that have no file yet, for the add control."""
    out = []
    for kind, label in KINDS:
        for key in _windows_for(kind):
            if not os.path.exists(core.period_prompt_path(key, kind=kind)):
                out.append({"kind": kind, "period": key,
                            "label": "{} - {}".format(label, key)})
    return out


@admin.route("/admin/prompts", methods=["GET", "POST"])
@requires_login
def prompts():
    config = Config()
    if request.method == "POST":
        if request.form.get("seed"):
            return _seed_override(config, request.form.get("add", ""))
        kind = request.form.get("kind")
        period = (request.form.get("period") or "").strip()
        if kind not in ("player", "group"):
            flash("Unknown prompt.")
        elif period and period not in _windows_for(kind):
            flash("A {} prompt is never asked for a {}.".format(kind, period))
        else:
            # A period names its own file; no period means the base one. Both
            # go through period_prompt_path/base_prompt_path rather than
            # prompt_path, which answers "which file would be *used*" and so
            # falls back to the base - saving an override through it would
            # quietly write the base file instead.
            path = (core.period_prompt_path(period, kind=kind) if period
                    else core.base_prompt_path(kind=kind))
            text = request.form.get("text", "").strip()
            if request.form.get("delete") and period:
                if os.path.exists(path):
                    os.remove(path)
                flash("Removed the {} override for {}; it falls back to the "
                      "base prompt.".format(kind, period))
            elif not text:
                flash("A prompt cannot be empty.")
            else:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "w", encoding="utf-8") as handle:
                    handle.write(text + "\n")
                flash("Saved the {} prompt{}.".format(
                    kind, " for " + period if period else ""))
        return redirect(url_for("admin.prompts"))
    return render_template("admin_prompts.html", page="admin",
                           prompts=_prompt_rows(config),
                           missing=_missing_overrides(config))


# -- things that take a while ---------------------------------------------

@admin.route("/admin/run/<action>", methods=["POST"])
@requires_login
def run(action):
    runner = current_app.config["JOBS"]
    database = current_app.config["DATABASE"]
    # Not named `scheduler`: this module imports the scheduler *module* under
    # that name, and shadowing it here made every reference in this function
    # ambiguous to read - which is why _stamp() below used to re-import
    # stamp_now rather than reach for the module it already had.
    slots = current_app.config.get("SCHEDULER")
    config = Config()

    def exclusive(body):
        """Wrap a job so it cannot overlap a scheduled run.

        JobRunner only stops two admin jobs colliding; the scheduler is a
        separate thread in this same process, and both touch the API and the
        same rows. The scheduler's flag is the one both sides check.
        """
        def work(job):
            if slots is not None and not slots.claim():
                job.finish("the scheduled update is running - try again shortly",
                           failed=True)
                return
            try:
                body(job)
            finally:
                if slots is not None:
                    slots.release()
        return work

    if action == "update":
        def work(job):
            client = WomClient(config.get("api_key", ""),
                               config.get("user_agent_contact", ""))
            names = config.get("usernames", [])
            if not names:
                job.finish("no players are being tracked", failed=True)
                return
            update_all(client, database, names, trigger="web",
                       starting=lambda i, n, name: job.say(
                           "{}/{}  {}".format(i, n, name)),
                       progress=lambda i, n, r: job.say(
                           "{}  {}".format(r.username, r.message), keep=True))
            config["last_run"] = scheduler.stamp_now()
            config.save()
            job.finish("update finished")
        started = runner.start("update", exclusive(work))

    elif action == "summarise":
        def work(job):
            owed = core.due_periods(database)
            if not owed:
                job.finish("every closed period already has a summary")
                return
            core.summarise_all(
                database, config, database.players(), owed,
                progress=lambda e: job.say(
                    "{}: {}".format(e["player"], e["note"]), keep=True))
            job.finish("summaries finished")
        started = runner.start("summarise", exclusive(work))

    elif action == "backfill":
        def work(job):
            client = WomClient(config.get("api_key", ""),
                               config.get("user_agent_contact", ""))
            for name in config.get("usernames", []):
                job.say("importing history for {}".format(name))
                count, note = backfill_player(client, database, name, force=True)
                job.say("{}: {}".format(name, note or "nothing to import"), keep=True)
            job.finish("history import finished")
        started = runner.start("backfill", exclusive(work))

    else:
        flash("Unknown action.")
        return redirect(url_for("admin.settings"))

    flash("Started." if started else "Something is already running.")
    return redirect(url_for("admin.settings"))


@admin.route("/admin/status")
@requires_login
def status():
    return jsonify(current_app.config["JOBS"].status())
