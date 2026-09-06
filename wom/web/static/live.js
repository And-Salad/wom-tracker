/* The header line, kept true: how fresh the data is and when the next run is.
 *
 * Everything on this site is drawn from readings the scheduler takes every ten
 * minutes, in the same process that serves the pages - but nothing ever told a
 * browser one had landed. A dashboard left open showed the figures it was
 * opened with, under a header that said "updated 2m ago" long after it was an
 * hour, which is worse than saying nothing.
 *
 * So: poll one cheap endpoint on a timer, and when the stamp it carries
 * changes, ask the page for the same view again. Polling rather than a pushed
 * stream because the server is waitress with eight worker threads, and an open
 * event stream holds one of them for as long as the tab lives - eight readers
 * and the site stops answering.
 *
 * A page that cannot refetch in place is offered a reload instead; it is never
 * taken out from under a reader. See refresh() below.
 */
(function () {
  "use strict";

  var head = document.getElementById("freshness");
  if (!head || !window.fetch) { return; }        // nothing to keep true

  var players = document.getElementById("stat-players");
  var lastBox = document.getElementById("stat-last");
  var nextBox = document.getElementById("stat-next");
  var button = document.getElementById("refresh-now");

  var QUIET = 60000;      // the usual gap between polls
  var DUE = 15000;        // once a slot has passed and a run is expected
  var CEILING = 600000;   // the longest an error backs us off to
  var PATIENCE = 300000;  // how long "updating" may stand before we disbelieve it

  /* What the server last told us, seeded from what it rendered into the page
     so the first tick is right rather than waiting a minute to become so. */
  var stamp = head.dataset.stamp || "";
  var nextAt = Date.parse(head.dataset.next || "") || 0;
  /* This browser's clock minus the server's. A countdown drawn from a clock
     that is five minutes fast would sit at "updating" forever, and the reading
     each answer carries is the only thing we can correct against. */
  var skew = 0;
  // What the header says when there is nothing to count down to.
  var plain = nextBox ? nextBox.textContent : "";

  var waiting = QUIET;    // the current gap, doubled by failures
  var overdueAt = 0;      // when the slot we are waiting on came due
  var poller = null;
  var ticker = null;

  function serverNow() {
    return Date.now() - skew;
  }

  // ---- the header -------------------------------------------------------

  function pad(n) {
    return n < 10 ? "0" + n : String(n);
  }

  function countdown(ms) {
    var total = Math.round(ms / 1000);
    if (total >= 3600) {
      return "next update in " + Math.floor(total / 3600) + "h " +
        pad(Math.floor((total % 3600) / 60)) + "m";
    }
    return "next update in " + Math.floor(total / 60) + ":" + pad(total % 60);
  }

  /* Redrawn once a second, and worked out from the clock every time rather
     than counted down - a tab asleep for an hour wakes showing the right
     number instead of the one it was parked on. */
  function tick() {
    if (!nextBox) { return; }
    if (overdueAt) {
      /* The slot came due and the readings have not landed yet. A run takes a
         few seconds, so this is the honest thing to say - but only for a
         while: served without a scheduler in the process it would stand all
         day, and then the wall-clock time is the better answer. */
      if (serverNow() - overdueAt < PATIENCE) {
        nextBox.textContent = "updating…";
        return;
      }
      overdueAt = 0;
      nextBox.textContent = plain;
      return;
    }
    if (!nextAt) {
      nextBox.textContent = plain;
      return;
    }
    var left = nextAt - serverNow();
    if (left > 0) {
      nextBox.textContent = countdown(left);
      return;
    }
    // Due. Say so, and start asking more often until the run answers for it.
    overdueAt = serverNow();
    nextBox.textContent = "updating…";
    schedule(DUE);
  }

  function show(status) {
    if (players && status.players !== undefined) {
      players.textContent = String(status.players);
    }
    // "3m ago" ages even when nothing has changed, which is most polls.
    if (lastBox && status.last) { lastBox.textContent = status.last; }
    if (status.next) { plain = "next " + status.next; }
    if (status.now) { skew = Date.now() - Date.parse(status.now); }
    if (status.next_at) { nextAt = Date.parse(status.next_at) || nextAt; }
    tick();
  }

  // ---- asking the page to catch up ---------------------------------------

  /* The pages that fetch their own figures - Overview, Leaderboards,
     Milestones, Players, Data - refetch in place and nothing else moves. The
     ones the server renders whole and that registered nothing (Recaps,
     Gallery) say so, and are given a link rather than a reload they did not
     ask for: both are documents somebody is part-way through reading. */
  function refresh() {
    var sidebar = window.Sidebar;
    if (sidebar && sidebar.refresh && sidebar.refresh()) { return; }
    if (button) { button.hidden = false; }
  }

  if (button) {
    button.addEventListener("click", function () { window.location.reload(); });
  }

  // ---- the poll ----------------------------------------------------------

  function schedule(ms) {
    clearTimeout(poller);
    poller = setTimeout(poll, ms);
  }

  /* Refused rather than broken. A tripped tripwire answers 503 with an hour
     on it, which is the server saying stop - so we stop, for however long it
     asked for, rather than picking a number of our own. */
  function refused(response) {
    var after = parseInt(response.headers.get("Retry-After") || "", 10);
    waiting = after > 0 ? Math.max(after * 1000, QUIET) : CEILING;
    schedule(waiting);
  }

  function failed() {
    // Doubling, because whatever is wrong will not be fixed by asking harder.
    // A page that cannot poll is the page we had before this file existed.
    waiting = Math.min(waiting * 2, CEILING);
    schedule(waiting);
  }

  function poll() {
    if (document.visibilityState === "hidden") { return; }
    return fetch("/api/status", {headers: {Accept: "application/json"}})
      .then(function (response) {
        if (!response.ok) {
          refused(response);
          return null;
        }
        return response.json();
      })
      .then(function (status) {
        if (!status) { return; }
        var changed = status.stamp && status.stamp !== stamp;
        stamp = status.stamp || stamp;
        if (changed) { overdueAt = 0; }
        show(status);
        if (changed) { refresh(); }
        // Keep asking often while a run is owed; otherwise settle back down.
        waiting = overdueAt ? DUE : QUIET;
        schedule(waiting);
      })
      .catch(failed);
  }

  // ---- when to run at all -------------------------------------------------

  /* Nothing happens in a tab nobody is looking at: no request, no timer. The
     moment one is looked at again it asks straight away, so a tab left open
     overnight is right by the time it is read rather than a minute after. */
  function start() {
    stop();
    ticker = setInterval(tick, 1000);
    tick();
    poll();
  }

  function stop() {
    clearTimeout(poller);
    clearInterval(ticker);
    poller = null;
    ticker = null;
  }

  document.addEventListener("visibilitychange", function () {
    if (document.visibilityState === "hidden") {
      stop();
      return;
    }
    waiting = QUIET;       // whatever backed us off, a reader is here now
    start();
  });

  if (document.visibilityState !== "hidden") { start(); }

  /* Named so the tests can drive one poll and one tick, rather than waiting a
     minute apiece for the timers to do it. */
  window.WOM = window.WOM || {};
  window.WOM.Live = {poll: poll, tick: tick, start: start, stop: stop};
})();
