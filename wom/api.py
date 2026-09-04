"""Thin client for the Wise Old Man v2 API (https://docs.wiseoldman.net/api)."""

import logging
import threading
import time
import urllib.parse
from datetime import datetime, timedelta, timezone

import requests

from .util import api_stamp

log = logging.getLogger(__name__)

BASE_URL = "https://api.wiseoldman.net/v2"

# The snapshots endpoint pages; 200 is the largest page it will hand back.
SNAPSHOT_PAGE_SIZE = 200

# Ceiling on how much history one import will pull. Pages come back newest
# first, so hitting this drops the oldest snapshots, not the useful recent ones.
SNAPSHOT_MAX_PAGES = 25
HISTORY_LIMIT = SNAPSHOT_PAGE_SIZE * SNAPSHOT_MAX_PAGES

# Far enough back to cover any history Wise Old Man holds, imported or not.
HISTORY_START = datetime(2013, 1, 1, tzinfo=timezone.utc)

# The API allows 20 requests/minute anonymously and 100/minute with an API key.
ANON_REQUESTS_PER_MIN = 20
KEYED_REQUESTS_PER_MIN = 100


class WomError(Exception):
    """An API call failed. `status` is the HTTP code, or None for transport errors."""

    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


class WomClient:
    def __init__(self, api_key="", contact="", timeout=30, session=None):
        self.api_key = (api_key or "").strip()
        self.contact = (contact or "").strip()
        self.timeout = timeout
        self.session = session or requests.Session()
        per_min = KEYED_REQUESTS_PER_MIN if self.api_key else ANON_REQUESTS_PER_MIN
        # Leave a little headroom so a burst never trips the limiter.
        self._min_interval = 60.0 / per_min * 1.1
        self._next_allowed = 0.0
        self._throttle_lock = threading.Lock()

    # -- plumbing ---------------------------------------------------------

    def _headers(self):
        agent = "WOM-Tracker/1.0"
        if self.contact:
            agent += " ({})".format(self.contact)
        headers = {"User-Agent": agent, "Accept": "application/json"}
        if self.api_key:
            headers["x-api-key"] = self.api_key
        return headers

    def _throttle(self):
        with self._throttle_lock:
            wait = self._next_allowed - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self._next_allowed = time.monotonic() + self._min_interval

    def _request(self, method, path, params=None, attempts=3):
        url = BASE_URL + path
        last = None
        for attempt in range(attempts):
            self._throttle()
            try:
                resp = self.session.request(
                    method, url, params=params, headers=self._headers(),
                    timeout=self.timeout,
                )
            except requests.RequestException as exc:
                last = WomError("network error: {}".format(exc))
                time.sleep(2 ** attempt)
                continue

            if resp.status_code == 429:
                # Respect Retry-After when the server sends one.
                delay = (float(resp.headers.get("Retry-After") or 0)
                         or 15 * (attempt + 1))
                last = WomError("rate limited by the API", 429)
                time.sleep(delay)
                continue

            if 500 <= resp.status_code < 600:
                last = WomError("server error {}".format(resp.status_code),
                                resp.status_code)
                time.sleep(2 ** attempt)
                continue

            # A key the API rejects is worse than no key at all: it answers
            # 403 to every request and the tracker goes quiet, while the same
            # request without it is served. So it is dropped, loudly, and the
            # run carries on at the anonymous rate - which is ample here.
            if (resp.status_code == 403 and self.api_key
                    and "api key" in _error_message(resp).lower()):
                log.warning("Wise Old Man rejected the configured API key; "
                            "continuing without it. Clear or correct it under "
                            "/admin - anonymous is 20 requests a minute, and a "
                            "run of six players is twelve every ten.")
                self.api_key = ""
                continue

            if resp.status_code >= 400:
                raise WomError(_error_message(resp), resp.status_code)

            try:
                return resp.json()
            except ValueError as exc:
                raise WomError("the API returned a non-JSON response",
                               resp.status_code) from exc

        raise last or WomError("request failed")

    # -- endpoints --------------------------------------------------------

    def update_player(self, username):
        """POST /players/{username} - refresh from the hiscores, return details.

        This also registers the player with Wise Old Man if they are new.
        """
        return self._request("POST", "/players/" + _quote(username))

    def get_player(self, username):
        """GET /players/{username} - details plus the latest snapshot."""
        return self._request("GET", "/players/" + _quote(username))

    def get_achievements(self, username):
        """GET /players/{username}/achievements - every milestone, with dates."""
        return self._request(
            "GET", "/players/{}/achievements".format(_quote(username)))

    def get_snapshots(self, username, period=None, start_date=None, end_date=None,
                      limit=SNAPSHOT_PAGE_SIZE, offset=0):
        """GET /players/{username}/snapshots - one page of historic snapshots.

        Pass either `period` ("day"/"week"/"month"/"year") or a start/end date
        pair; the API rejects a period it does not recognise, so ranges longer
        than a year have to use dates.
        """
        params = {"limit": limit, "offset": offset}
        if period:
            params["period"] = period
        if start_date:
            params["startDate"] = _iso(start_date)
        if end_date:
            params["endDate"] = _iso(end_date)
        return self._request(
            "GET", "/players/{}/snapshots".format(_quote(username)), params=params)

    def iter_snapshots(self, username, start_date=None, end_date=None,
                       max_pages=SNAPSHOT_MAX_PAGES):
        """Yield every snapshot in a range, newest first, paging as needed."""
        start_date = start_date or HISTORY_START
        end_date = end_date or datetime.now(timezone.utc) + timedelta(days=1)
        for page in range(max_pages):
            batch = self.get_snapshots(
                username, start_date=start_date, end_date=end_date,
                limit=SNAPSHOT_PAGE_SIZE, offset=page * SNAPSHOT_PAGE_SIZE)
            if not batch:
                return
            yield from batch
            if len(batch) < SNAPSHOT_PAGE_SIZE:
                return


def _iso(value):
    """Format a datetime the way the API wants its date parameters."""
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return api_stamp(value)
    return str(value)


def _quote(username):
    return urllib.parse.quote(str(username).strip(), safe="")


def _error_message(resp):
    try:
        body = resp.json()
    except ValueError:
        body = {}
    message = body.get("message") if isinstance(body, dict) else None
    return message or "HTTP {}".format(resp.status_code)
