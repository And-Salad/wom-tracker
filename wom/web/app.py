"""The application factory: configuration, hardening, and what is mounted.

Everything else lives beside it - the pages in pages.py, the JSON in api.py,
the export in exporting.py, the admin half in admin.py, the view models in
views.py. This file used to be all of them at once.
"""

import hashlib
import logging
import os
from datetime import datetime, timedelta, timezone

from flask import Flask, Response, request, session
from werkzeug.middleware.proxy_fix import ProxyFix

from .. import theme
from ..util import is_local_host
from ..config import DB_PATH
from ..db import Database
from .admin import PASSWORD_ENV, admin as admin_blueprint, admin_enabled
from .api import api as api_blueprint
from .dates import BadRequest
from .exporting import exporting as exporting_blueprint
from .hooks import hooks as hooks_blueprint
from .jobs import JobRunner
from .limits import ConfigLatch, Limits
from .pages import pages as pages_blueprint
from .selection import settings, status

log = logging.getLogger(__name__)

SESSION_DAYS = 14


def create_app(limits=None):
    app = Flask(__name__, static_folder="static", static_url_path="/static")
    # Only for the scheme: Fly terminates TLS, and without this every request
    # looks like plain HTTP from in here. The client address is resolved from a
    # header instead - ProxyFix reads the rightmost X-Forwarded-For entry,
    # which behind a proxy is the proxy's own hop.
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=0, x_proto=1)

    # Versioned asset URLs, so the browser may cache them for a year and
    # still pick up a deploy the moment one lands. Computed once, at startup:
    # the files cannot change under a running process.
    app.config["ASSETS"] = _asset_version(app.static_folder)
    app.config["SEND_FILE_MAX_AGE_DEFAULT"] = timedelta(days=365)

    app.config["DATABASE"] = Database(DB_PATH)
    app.config["JOBS"] = JobRunner()
    app.config["LIMITS"] = limits or Limits(latch=ConfigLatch())
    # Set by web_app.py when it starts the scheduler; None when the dashboard
    # is served without one, in which case there is nothing to collide with.
    app.config.setdefault("SCHEDULER", None)

    _configure_admin(app)
    app.register_blueprint(pages_blueprint)
    app.register_blueprint(api_blueprint)
    app.register_blueprint(exporting_blueprint)
    app.register_blueprint(hooks_blueprint)
    _add_hardening(app)
    _note_hook_attempts(app)
    _add_template_globals(app)
    _refuse_bad_dates(app)
    return app


def _refuse_bad_dates(app):
    """Answer an unusable date with the reason, on every route at once.

    The window is resolved before a page renders as well as before a JSON
    endpoint answers, so a typed date reaches routes that have no business
    each catching it. Handled here they all answer alike - and a 500 for a
    mistyped query string was the alternative.
    """
    @app.errorhandler(BadRequest)
    def bad_date(exc):
        return Response(str(exc), status=400, mimetype="text/plain")


def _configure_admin(app):
    """Admin exists only when a password does - fail closed, not open."""
    app.config["ADMIN"] = admin_enabled()
    if not app.config["ADMIN"]:
        log.warning("%s is not set: the admin pages are disabled", PASSWORD_ENV)
        return
    app.secret_key = _session_key()
    app.permanent_session_lifetime = timedelta(days=SESSION_DAYS)
    # Every admin action is a form POST authenticated by this cookie alone.
    # Lax is what stops another site POSTing one on a signed-in viewer's
    # behalf; browsers default to it, but that is their choice rather than
    # ours until it is said here.
    app.config.update(SESSION_COOKIE_SAMESITE="Lax",
                      SESSION_COOKIE_HTTPONLY=True,
                      SESSION_COOKIE_SECURE=_https_only())
    app.register_blueprint(admin_blueprint)


def _add_hardening(app):
    @app.after_request
    def harden(response):
        """Headers the browser should enforce, since the link is public."""
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data:; "
            "style-src 'self' 'unsafe-inline'; script-src 'self'; "
            "form-action 'self'; base-uri 'none'; frame-ancestors 'none'")
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        # request.is_secure cannot be trusted: waitress strips
        # X-Forwarded-Proto with the rest of the forwarded headers, so every
        # request looks like plain HTTP from inside. Anything not on a local
        # hostname is reached over HTTPS in practice, and pinning HSTS on
        # localhost would only make development painful.
        if request.is_secure or not is_local_host(request.host):
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        return response


def _note_hook_attempts(app):
    """Say that something reached /hook, whatever shape it arrived in.

    hooks.py logs the calls that route correctly. This catches the ones that
    do not - the wrong method, a stale path, a URL sent over plain HTTP and
    turned into a GET by the redirect - because otherwise every one of them
    is silence, and silence looks exactly like a plugin that never sent
    anything. Which of those two it is has to be answerable from a log.

    The last path segment is a secret, so only its length is written down.
    """
    @app.before_request
    def note():
        if not request.path.startswith("/hook"):
            return None
        head, _, tail = request.path.rpartition("/")
        log.info("hook: %s %s/<%d characters> ua=%r", request.method, head,
                 len(tail), (request.headers.get("User-Agent") or "?")[:60])
        return None


def _add_template_globals(app):
    @app.context_processor
    def helpers():
        # One palette for the page and the D3 charts alike.
        declarations = ["    {}: {};".format(name, value)
                        for name, value in theme.css_variables().items()]
        return {"now": datetime.now(timezone.utc),
                "assets": app.config["ASSETS"],
                "css_variables": "\n".join(declarations),
                "admin_enabled": app.config["ADMIN"],
                "signed_in": bool(session.get("wom_admin")),
                # The header carries this on every page, admin included, so it
                # is supplied here once rather than by each view in turn. The
                # public pages used to pass their own as a template argument
                # too, which wins over a context processor - so this was
                # computed, and its settings file re-read, only to be thrown
                # away on every one of those renders.
                "status": status(settings())}


def _asset_version(folder):
    """A short token that changes whenever a static file does."""
    digest = hashlib.sha1()
    for name in sorted(os.listdir(folder or ".")):
        try:
            stat = os.stat(os.path.join(folder, name))
        except OSError:
            continue
        digest.update("{}:{}:{}".format(name, stat.st_mtime_ns, stat.st_size)
                      .encode("utf-8"))
    return digest.hexdigest()[:8]


def _https_only():
    """Mark the cookie Secure unless this is a plain-HTTP local run."""
    return os.environ.get("WOM_INSECURE_COOKIE", "").strip().lower() not in (
        "1", "true", "yes")


def _session_key():
    """The key that signs the admin cookie.

    Set WOM_SECRET_KEY to keep sessions alive across restarts. Without one a
    fresh key is minted per process, which is safe but signs everyone out
    whenever the server restarts.
    """
    given = os.environ.get("WOM_SECRET_KEY", "").strip()
    if given:
        return given
    log.info("WOM_SECRET_KEY is not set; admin sessions end when this "
             "process does")
    return os.urandom(32)
