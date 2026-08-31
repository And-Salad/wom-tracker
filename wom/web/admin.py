"""The admin side of the dashboard: everything the desktop app used to own.

The public pages stay read-only and unauthenticated - that is the whole point
of the share link. Everything here changes state, so it all sits behind a
password. Fail closed: with no password configured the admin routes do not
merely reject requests, they are never registered, so a deployment that forgets
to set one is not quietly wide open.
"""

import hmac
import logging
import os
from functools import wraps

from flask import (Blueprint, current_app, flash, redirect, render_template,
                   request, session, url_for)

from .. import periods, summaries as core
from ..colors import normalise, player_color, set_player_color
from ..config import Config, ENV_KEYS, normalise_usernames
from ..summaries import SUMMARY_MODELS

log = logging.getLogger(__name__)

admin = Blueprint("admin", __name__)

PASSWORD_ENV = "WOM_ADMIN_PASSWORD"


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
        given = request.form.get("password", "")
        # compare_digest rather than ==: no early exit, so a wrong guess tells
        # an attacker nothing about how much of it was right.
        if hmac.compare_digest(given, admin_password()):
            session["wom_admin"] = True
            session.permanent = True
            return redirect(request.args.get("next") or url_for("admin.settings"))
        error = "That is not the password."
        log.warning("failed admin sign-in from %s", request.remote_addr)
    return render_template("admin_login.html", error=error, page="admin")


@admin.route("/admin/logout", methods=["POST"])
def logout():
    session.pop("wom_admin", None)
    return redirect(url_for("dashboard"))


# -- settings -------------------------------------------------------------

@admin.route("/admin", methods=["GET"])
@requires_login
def settings():
    config = Config()
    database = current_app.config["DATABASE"]
    players = database.players()
    known = {p["username"]: p for p in players}
    roster = []
    for index, name in enumerate(config.get("usernames", [])):
        row = known.get(name.lower())
        roster.append({
            "username": name,
            "display_name": row["display_name"] if row is not None else name,
            "color": player_color(config, name, index),
            "snapshots": database.snapshot_count(row["id"]) if row is not None else 0,
            "updated": row["updated_at"] if row is not None else None,
        })
    return render_template(
        "admin.html", page="admin", config=config, roster=roster,
        models=SUMMARY_MODELS, env_keys=ENV_KEYS,
        job=current_app.config["JOBS"].status(),
        periods=[p.key for p in periods.PERIODS])


@admin.route("/admin/settings", methods=["POST"])
@requires_login
def save_settings():
    config = Config()
    names = normalise_usernames(request.form.get("usernames", "").splitlines())
    config["usernames"] = names
    config["summaries_enabled"] = bool(request.form.get("summaries_enabled"))
    config["summary_model"] = request.form.get("summary_model") or "claude-sonnet-5"
    config["user_agent_contact"] = request.form.get("user_agent_contact", "").strip()
    # A key supplied by the environment is not editable here, and a blank box
    # means "leave it alone" rather than "erase it".
    for key in ("api_key", "anthropic_api_key"):
        if config.is_from_env(key):
            continue
        given = request.form.get(key, "").strip()
        if given:
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

@admin.route("/admin/prompts", methods=["GET", "POST"])
@requires_login
def prompts():
    config = Config()
    kinds = (("player", "Per-player prompt"), ("group", "Group round-up prompt"))
    if request.method == "POST":
        kind = request.form.get("kind")
        if kind in ("player", "group"):
            path = core.prompt_path(config, None, kind=kind)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(request.form.get("text", "").strip() + "\n")
            flash("Saved the {} prompt.".format(kind))
        return redirect(url_for("admin.prompts"))
    loaded = [{"kind": kind, "title": title,
               "text": core.load_prompt(config, None, kind=kind)}
              for kind, title in kinds]
    return render_template("admin_prompts.html", page="admin", prompts=loaded)


# -- things that take a while ---------------------------------------------

@admin.route("/admin/run/<action>", methods=["POST"])
@requires_login
def run(action):
    runner = current_app.config["JOBS"]
    database = current_app.config["DATABASE"]
    config = Config()

    if action == "update":
        def work(job):
            from ..api import WomClient
            from ..updater import update_all
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
            config["last_run"] = _stamp()
            config.save()
            job.finish("update finished")
        started = runner.start("update", work)

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
        started = runner.start("summarise", work)

    elif action == "backfill":
        def work(job):
            from ..api import WomClient
            from ..updater import backfill_player
            client = WomClient(config.get("api_key", ""),
                               config.get("user_agent_contact", ""))
            for name in config.get("usernames", []):
                job.say("importing history for {}".format(name))
                count, note = backfill_player(client, database, name, force=True)
                job.say("{}: {}".format(name, note or "nothing to import"), keep=True)
            job.finish("history import finished")
        started = runner.start("backfill", work)

    else:
        flash("Unknown action.")
        return redirect(url_for("admin.settings"))

    flash("Started." if started else "Something is already running.")
    return redirect(url_for("admin.settings"))


@admin.route("/admin/status")
@requires_login
def status():
    from flask import jsonify
    return jsonify(current_app.config["JOBS"].status())


def _stamp():
    from ..scheduler import stamp_now
    return stamp_now()
