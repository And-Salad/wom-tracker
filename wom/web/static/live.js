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

  var viewers = document.getElementById("stat-viewers");
  var lastBox = document.getElementById("stat-last");
  var nextBox = document.getElementById("stat-next");
  var button = document.getElementById("refresh-now");

  var QUIET = 60000;      // the usual gap between polls
  /* Once a slot has passed the readings are seconds away, so the gap collapses
     rather than staying at one flat number. SOON is the first look and DUE the
     widest it opens back out to, easing off in between - a run takes a few
     seconds and occasionally a few minutes, and a flat fifteen meant a reader
     watching the header saw a finished run up to fifteen seconds after it
     landed, for the sake of gaps that are only ever spent while an update is
     actually owed. Five extra requests per slot, against a budget of six
     hundred per five minutes, is not a cost worth that delay. */
  var SOON = 3000;
  var EAGER = 45000;      // how long a run gets SOON before the gap opens out
  var DUE = 15000;
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
  var owedGap = SOON;     // the current gap while a run is owed, easing out
  var overdueAt = 0;      // when the slot we are waiting on came due
  /* Set while the gap we are sitting out is one the server or the network
     asked for rather than our usual. tick() reads it - without it, the clock
     passing a slot quietly undid an hour of Retry-After, every five minutes,
     for as long as the tab stayed open. */
  var holding = false;
  /* When a poll last came back. "updating…" is a claim about right now, and
     nothing that has not heard from the server for PATIENCE is in a position
     to make it - see tick(). Seeded as if we had just heard, because the
     server rendered the header we are standing on. */
  var heardAt = 0;
  /* Bumped whenever the poll stops or starts. An answer stamped with an older
     one is from a fetch nobody is waiting on any more - a tab that went away
     and came back, or one alt-tabbed at a few times - and it must not draw
     over a newer answer or arm a timer in a tab we have just stopped. */
  var epoch = 0;
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
         few seconds, so this is the honest thing to say - but only while we
         are in a position to say it. Two ways we are not.

         One, it has stood too long: served without a scheduler in the process
         nothing will ever land, and "updating" would stand all day.

         Two, and this is the one readers actually hit: we have not heard from
         the server in as long. A page whose polls are failing has a countdown
         frozen wherever it stopped, which runs out and never restarts - so it
         said "updating…" for five minutes, blinked, and said it again, for as
         long as the tab stayed open, while the site itself had been updating
         all along. It was not describing a run. It was describing its own
         silence, and a reader could only find out by reloading. */
      if (serverNow() - overdueAt < PATIENCE &&
          serverNow() - heardAt < PATIENCE) {
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
    /* Due - but the same question as above, asked on the way in rather than
       on the way out. Without it the two halves disagreed once the polls
       stopped: this one entered the wait on a countdown that had run out, the
       one above threw it straight back out again for not having heard from
       the server, and the header flickered between "updating…" and a time,
       once a second, for as long as the tab stayed open. */
    if (serverNow() - heardAt >= PATIENCE) {
      nextBox.textContent = plain;
      return;
    }
    // Say so, and start asking more often until the run answers for it.
    overdueAt = serverNow();
    owedGap = SOON;
    nextBox.textContent = "updating…";
    /* Unless we are sitting out a gap the server named. A slot coming due is
       not news to a dashboard that has told us it is paused, and asking anyway
       turned an hour of Retry-After into another request every five minutes. */
    if (!holding) { schedule(owedGap); }
  }

  function show(status, changed) {
    /* The count includes whoever is reading this, so it is never 0 while
       anyone can see it - but "1 viewers" is, so the word comes from here
       rather than being left standing in the markup beside a number. */
    if (viewers && status.viewers !== undefined) {
      viewers.textContent = status.viewers +
        (status.viewers === 1 ? " viewer" : " viewers");
    }
    // "3m ago" ages even when nothing has changed, which is most polls.
    if (lastBox && status.last) { lastBox.textContent = status.last; }
    if (status.next) { plain = "next " + status.next; }
    if (status.now) { skew = Date.now() - Date.parse(status.now); }
    heardAt = serverNow();
    if (status.next_at) {
      /* The server names the next slot, so a slot moving on is the server
         saying the last one has passed - which beats the ticker noticing it,
         and does not race the poll that arrives in the same second.

         That race was costing whole updates. A poll landing between the
         boundary and the tick took the countdown straight on to the next slot
         and tick() then had nothing to run out, so the tight chain below was
         never started and the run was found on the ordinary minute - up to
         forty seconds after it landed, on a page built to notice in three. */
      var moved = Date.parse(status.next_at) || nextAt;
      /* A whole second later, not merely later: within one slot every answer
         names the same instant to the second, but the one the server rendered
         into the page can sit a few milliseconds off the one it later sends,
         and a bare `>` read that as a slot going by. Slots are ten minutes
         apart, so there is nothing here a second could miss. */
      if (nextAt && moved - nextAt > 1000 && !changed) {
        overdueAt = overdueAt || serverNow();
        owedGap = SOON;
      }
      nextAt = moved;
    }
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
    holding = true;
    schedule(waiting);
  }

  function failed() {
    // Doubling, because whatever is wrong will not be fixed by asking harder.
    // A page that cannot poll is the page we had before this file existed.
    waiting = Math.min(waiting * 2, CEILING);
    holding = true;
    schedule(waiting);
  }

  function poll() {
    if (document.visibilityState === "hidden") { return; }
    var mine = epoch;
    function current() { return mine === epoch; }
    return fetch("/api/status", {headers: {Accept: "application/json"}})
      .then(function (response) {
        if (!current()) { return null; }
        if (!response.ok) {
          refused(response);
          return null;
        }
        return response.json();
      })
      .then(function (status) {
        if (!status || !current()) { return; }
        holding = false;
        var changed = status.stamp && status.stamp !== stamp;
        stamp = status.stamp || stamp;
        if (changed) { overdueAt = 0; }
        show(status, changed);
        if (changed) { refresh(); }
        /* Keep asking while a run is owed, easing off as it goes on: the
           readings usually land within the first few seconds, and a wait that
           is still going after a minute is not one worth hammering. */
        if (overdueAt) {
          /* Tight for as long as a run plausibly takes, and only then easing
             off. Easing from the first poll was barely worth having: a pass
             over six players takes twenty-odd seconds, by which point a gap
             that grows by half each time is already back up at twelve, and
             the run was found nine seconds late instead of ten. The gaps
             worth spending are the early ones. */
          owedGap = serverNow() - overdueAt < EAGER
            ? SOON : Math.min(Math.round(owedGap * 1.6), DUE);
          waiting = owedGap;
        } else {
          /* Otherwise sleep until just past the slot, rather than through it.
             A flat minute meant the poll that found a run was whichever one
             happened to fall after it, so the same page noticed one update in
             three seconds and the next in fifty. Capped at QUIET because "3m
             ago" and the viewer count age whether a run is owed or not, and
             floored at SOON so a boundary already on top of us is not chased
             in tiny steps. */
          waiting = QUIET;
          if (nextAt) {
            waiting = Math.min(waiting,
                               Math.max(nextAt - serverNow() + 1000, SOON));
          }
        }
        schedule(waiting);
      })
      .catch(function () {
        if (current()) { failed(); }
      });
  }

  // ---- when to run at all -------------------------------------------------

  /* Nothing happens in a tab nobody is looking at: no request, no timer. The
     moment one is looked at again it asks straight away, so a tab left open
     overnight is right by the time it is read rather than a minute after. */
  function start() {
    stop();
    epoch += 1;            // anything still in flight was asked for by nobody
    holding = false;       // a reader is here: whatever backed us off, ask now
    heardAt = serverNow(); // and nothing is stale until this poll says so
    ticker = setInterval(tick, 1000);
    tick();
    poll();
  }

  function stop() {
    clearTimeout(poller);
    clearInterval(ticker);
    epoch += 1;            // and no answer still owed may arm a timer here
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

  /* Back and forward are not a fresh load. A page restored from the browser's
     cache comes back with its timers frozen and the figures it was left with,
     and fires none of the events above - so it sat there stale until the first
     unfrozen timer got round to it, which is the state this file exists to
     prevent. */
  window.addEventListener("pageshow", function (event) {
    if (!event.persisted || document.visibilityState === "hidden") { return; }
    waiting = QUIET;
    start();
  });

  /* A dropped connection doubles the gap up to ten minutes, which is the right
     answer for as long as it is dropped and the wrong one the moment it is
     not: the browser knows the network is back long before we would have got
     round to asking again. */
  window.addEventListener("online", function () {
    if (document.visibilityState === "hidden") { return; }
    waiting = QUIET;
    start();
  });

  if (document.visibilityState !== "hidden") { start(); }

  /* Named so the tests can drive one poll and one tick, rather than waiting a
     minute apiece for the timers to do it. */
  window.WOM = window.WOM || {};
  window.WOM.Live = {poll: poll, tick: tick, start: start, stop: stop};
})();
