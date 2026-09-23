/* live.js: the header that keeps itself true, and the refresh behind it.
 *
 * Two promises worth holding it to. One, an open page notices a finished
 * update and asks for the same view again - and does not ask when nothing has
 * changed, because the poll is on a timer and most of them change nothing.
 * Two, a page that cannot redraw in place is offered a reload rather than
 * given one, which is the whole difference between Overview and Recaps.
 *
 * The timers are recorded rather than waited on: every gap this file chooses
 * is at least fifteen seconds, and a suite that slept through them would take
 * minutes to say what reading the delays says at once. The clock is ours too,
 * so a countdown can be watched running out without the wait.
 */
"use strict";

const test = require("node:test");
const assert = require("node:assert");
const { page, source } = require("./page");

const HEADER = `<!doctype html><html><body><header>
  <span class="status" id="freshness" data-stamp="STAMP" data-next="NEXT">
    <span id="stat-viewers">2 viewers</span> &middot;
    updated <span id="stat-last">3m ago</span> &middot;
    <span id="stat-next">next Wed 14:20</span>
  </span>
  <button type="button" class="link" id="refresh-now" hidden>New data</button>
</header></body></html>`;

/* What the server answers, in the shape selection.status() builds. */
function status(fields) {
  return Object.assign({viewers: 2, last: "just now", next: "Wed 14:30",
                        next_at: null, now: null, stamp: "seen"}, fields || {});
}

/* A sidebar that says whether it could refetch and counts the asking - which
   is the whole difference between Overview and Recaps. */
function sidebar(canRefetch) {
  const calls = {n: 0};
  return {calls: calls,
          stub: {refresh: function () { calls.n += 1; return canRefetch; }}};
}

/* A page with live.js on it, its clock and its network under our control.
 *
 * `next` seeds the countdown the way the server's rendered attribute does, and
 * is given in milliseconds from now. The opening poll every load does is
 * settled before this returns, so a test measures what it asked for rather
 * than that.
 */
async function live(options) {
  const settings = options || {};
  const dom = page(HEADER
    .replace("STAMP", settings.stamp === undefined ? "seen" : settings.stamp)
    .replace("NEXT", at(settings.next === undefined ? 300000 : settings.next)),
                   []);
  const win = dom.window;

  // Ours, so a countdown can be watched running out. Everything in live.js
  // reads the time through Date.now().
  //
  // Frozen, and moved only by advance(). It was the real clock plus an
  // offset, so a millisecond ticking between two reads inside one poll made
  // a wake eight seconds out come back as 8999 - once in a while, which is
  // how it failed a deploy and passed on the rerun.
  let moved = 0;
  win.Date.now = function () { return FROZEN + moved; };

  // Recorded, not run: nothing in this file schedules itself during a test.
  const delays = [];
  win.setTimeout = function (_fn, ms) { delays.push(ms); return 1; };
  win.setInterval = function () { return 2; };
  win.clearTimeout = function () {};
  win.clearInterval = function () {};

  const asked = [];
  win.fetch = function (url) {
    asked.push(url);
    if (settings.offline) { return Promise.reject(new Error("offline")); }
    const reply = settings.reply || {};
    return Promise.resolve({
      ok: !reply.status || reply.status === 200,
      headers: {get: function (name) {
        return (reply.headers || {})[name] || null;
      }},
      json: function () { return Promise.resolve(reply.body || {}); },
    });
  };

  if (settings.sidebar) { win.Sidebar = settings.sidebar; }
  win.eval(source("live.js"));

  const it = {
    dom: dom, win: win, delays: delays, asked: asked, live: win.WOM.Live,
    settings: settings,
    text: function (id) {
      return win.document.getElementById(id).textContent;
    },
    offered: function () {
      return !win.document.getElementById("refresh-now").hidden;
    },
    advance: function (ms) { moved += ms; },
    // Everything the load did, so a test measures only what it asked for.
    clear: function () { delays.length = 0; asked.length = 0; return it; },
  };
  await settle();
  return it;
}

/* The opening poll is two resolved promises deep; a real turn of the loop is
   past both. Node's own setTimeout, not the window's recorded one. */
function settle() {
  return new Promise(function (resolve) { setTimeout(resolve, 0); });
}

/* The instant every page in this file believes it is, before advance(). One
   for the whole file, so a time a test writes with at() and the page's own
   reading of now can never drift apart while the test runs. */
const FROZEN = Date.now();

function at(offsetMs) {
  return new Date(FROZEN + offsetMs).toISOString();
}

// -- what a poll does ------------------------------------------------------

test("a poll that changes nothing does not disturb the page", async () => {
  const bar = sidebar(true);
  const it = await live({stamp: "seen", sidebar: bar.stub,
                         reply: {body: status({stamp: "seen", last: "4m ago"})}});
  await it.clear().live.poll();
  assert.strictEqual(bar.calls.n, 0, "nothing landed, so nothing to redraw");
  assert.strictEqual(it.text("stat-last"), "4m ago",
                     "but how old it is still ages");
  assert.deepStrictEqual(it.delays, [60000], "and it asks again in a minute");
});

test("the header says how many people are here, and says it in English", async () => {
  const it = await live({stamp: "seen",
                         reply: {body: status({stamp: "seen", viewers: 1})}});
  assert.strictEqual(it.text("stat-viewers"), "1 viewer",
                     "\"1 viewers\" is why the word is written here");
  await it.clear().live.poll();
  assert.strictEqual(it.text("stat-viewers"), "1 viewer");
});

test("a finished run is what makes the page ask again", async () => {
  const bar = sidebar(true);
  const it = await live({stamp: "seen", sidebar: bar.stub,
                         reply: {body: status({stamp: "newer"})}});
  assert.strictEqual(bar.calls.n, 1);
  assert.strictEqual(it.offered(), false,
                     "it redrew in place, so there is nothing to offer");

  // And only once: the stamp it now holds is the one it was told.
  await it.live.poll();
  assert.strictEqual(bar.calls.n, 1);
});

test("a page that cannot redraw is offered a reload rather than given one",
     async () => {
       const bar = sidebar(false);        // Recaps: data-reload, no listeners
       const it = await live({stamp: "seen", sidebar: bar.stub,
                              reply: {body: status({stamp: "newer"})}});
       assert.strictEqual(bar.calls.n, 1, "it was asked");
       assert.strictEqual(it.offered(), true);
     });

test("a page with no sidebar at all is offered the same link", async () => {
  const it = await live({stamp: "seen", reply: {body: status({stamp: "newer"})}});
  assert.strictEqual(it.offered(), true,
                     "Gallery and Help have nothing registered");
});

// -- the countdown ---------------------------------------------------------

test("the countdown replaces the wall-clock time it was rendered with",
     async () => {
       const it = await live({next: 300000});
       it.live.tick();
       assert.match(it.text("stat-next"), /^next update in (4:59|5:00)$/);
     });

test("an hour away is said in hours, not in minutes", async () => {
  const it = await live({next: 3660000});
  it.live.tick();
  assert.match(it.text("stat-next"), /^next update in 1h 0[01]m$/);
});

test("it counts down as the clock moves", async () => {
  const it = await live({next: 300000});
  it.advance(120000);
  it.live.tick();
  assert.match(it.text("stat-next"), /^next update in (2:59|3:00)$/);
});

test("past the slot it says so, and starts asking more often", async () => {
  const it = await live({next: 1000});
  it.clear().advance(2000);
  it.live.tick();
  assert.strictEqual(it.text("stat-next"), "updating…");
  assert.deepStrictEqual(it.delays, [3000],
                         "a run is owed, so the next look is seconds away");
});

test("the gap stays tight for as long as a run plausibly takes",
     async () => {
       /* A pass over six players takes twenty-odd seconds, so a gap that eased
          off from the first poll was back up at twelve by the time there was
          anything to find. The early gaps are the ones worth spending. */
       const it = await live({next: 1000, stamp: "seen",
                              reply: {body: status({stamp: "seen"})}});
       it.advance(2000);
       it.live.tick();
       assert.strictEqual(it.text("stat-next"), "updating…");

       it.clear();
       for (let n = 0; n < 4; n += 1) { await it.live.poll(); it.advance(3000); }
       assert.deepStrictEqual(it.delays, [3000, 3000, 3000, 3000],
                              "three seconds apart while a run is owed");
     });

test("a wait that goes on and on is not hammered", async () => {
  const it = await live({next: 1000, stamp: "seen",
                         reply: {body: status({stamp: "seen"})}});
  it.advance(2000);
  it.live.tick();

  it.clear().advance(50000);           // past the point a run should have landed
  for (let n = 0; n < 5; n += 1) { await it.live.poll(); }
  assert.deepStrictEqual(it.delays, [4800, 7680, 12288, 15000, 15000],
                         "easing off, and never wider than fifteen seconds");
});

test("it wakes for the slot rather than sleeping through it", async () => {
  /* A flat minute meant the poll that found a run was whichever one happened
     to fall after it, so the same page noticed one update in three seconds
     and the next in fifty. */
  const it = await live({next: 300000, stamp: "seen"});
  it.clear().settings.reply = {body: status(
    {stamp: "seen", next_at: at(8000), now: at(0)})};
  await it.live.poll();
  assert.deepStrictEqual(it.delays, [9000],
                         "the slot is eight seconds away, so look then");
});

test("a slot a long way off is still only worth a minute", async () => {
  /* How old the readings are and how many people are here both age whether a
     run is owed or not, so the gap has a ceiling as well as a floor. */
  const it = await live({next: 300000, stamp: "seen"});
  it.clear().settings.reply = {body: status(
    {stamp: "seen", next_at: at(300000), now: at(0)})};
  await it.live.poll();
  assert.deepStrictEqual(it.delays, [60000]);
});

test("the server's word is what says a slot has passed, not our ticker",
     async () => {
       /* A poll landing between the boundary and the tick took the countdown
          straight on to the next slot, and tick() then had nothing left to run
          out - so the tight chain never started and the run was found on the
          ordinary minute instead. */
       const it = await live({next: 300000, stamp: "seen",
                              reply: {body: status({stamp: "seen",
                                                    next_at: at(300000),
                                                    now: at(0)})}});
       it.clear().settings.reply = {body: status(
         {stamp: "seen", next_at: at(900000), now: at(0)})};   // a slot went by
       await it.live.poll();
       assert.strictEqual(it.text("stat-next"), "updating…",
                          "the slot moved on and no run came with it");
       assert.deepStrictEqual(it.delays, [3000]);
     });

test("a slot coming due does not undo a refusal", async () => {
  /* The gap after a 503 is one the server named. tick() reaching a slot used
     to overwrite it, which turned an hour of Retry-After into another request
     every five minutes for as long as the tab stayed open. */
  const it = await live({next: 1000});
  it.clear().settings.reply = {status: 503, headers: {"Retry-After": "3600"}};
  await it.live.poll();
  assert.deepStrictEqual(it.delays, [3600000]);

  it.clear().advance(2000);
  it.live.tick();
  assert.strictEqual(it.text("stat-next"), "updating…", "it still says so");
  assert.deepStrictEqual(it.delays, [], "but it does not ask ahead of the hour");
});

test("a run landing ends the wait and settles the gap back down", async () => {
  const it = await live({next: 1000, stamp: "seen",
                         reply: {body: status({stamp: "seen"})}});
  it.advance(2000);
  it.live.tick();
  assert.strictEqual(it.text("stat-next"), "updating…");

  it.clear().settings.reply = {body: status(
    {stamp: "newer", next_at: at(600000), now: at(0)})};
  await it.live.poll();
  assert.match(it.text("stat-next"), /^next update in (9:59|10:00)$/);
  assert.deepStrictEqual(it.delays, [60000]);
});

test("a page that cannot reach the server does not claim it is updating",
     async () => {
       /* The report this came from: a header stuck on "updating…" while the
          site had been updating all along, put right by a reload. A page whose
          polls fail has a frozen countdown, which runs out and never restarts
          - and "updating" is a claim about right now that nothing out of touch
          with the server is in a position to make. */
       const it = await live({next: 1000});
       it.clear().settings.offline = true;
       it.advance(2000);
       it.live.tick();
       assert.strictEqual(it.text("stat-next"), "updating…",
                          "a run really might be landing; we only just heard");

       it.advance(6 * 60000);           // now we have not heard in a long time
       it.live.tick();
       assert.strictEqual(it.text("stat-next"), "next Wed 14:20");
       it.live.tick();
       assert.strictEqual(it.text("stat-next"), "next Wed 14:20",
                          "and it stays there rather than flickering back");
     });

test("nothing counts down forever: with no scheduler the clock comes back",
     async () => {
       /* Served without --with-scheduler nothing will ever land, and a header
          reading "updating" all day is worse than one naming the slot. */
       const it = await live({next: 1000});
       it.advance(2000);
       it.live.tick();
       assert.strictEqual(it.text("stat-next"), "updating…");

       it.advance(6 * 60000);
       it.live.tick();
       assert.strictEqual(it.text("stat-next"), "next Wed 14:20");
     });

test("the countdown follows the server's clock, not this one", async () => {
  /* A browser five minutes fast counts the next slot as one that has already
     passed, and sits at "updating" forever. Each answer carries the server's
     own reading of now, which is what corrects for that. */
  const it = await live({next: 300000});
  it.advance(300000);                            // this browser runs fast
  it.clear().settings.reply = {body: status(
    {next_at: at(300000), now: at(0), stamp: "seen"})};
  await it.live.poll();
  assert.match(it.text("stat-next"), /^next update in (4:59|5:00)$/);
});

// -- when it asks, and when it stops ---------------------------------------

test("a refusal is honoured for as long as it asks for", async () => {
  const it = await live({});
  it.clear().settings.reply = {status: 503, headers: {"Retry-After": "3600"}};
  await it.live.poll();
  assert.deepStrictEqual(it.delays, [3600000],
                         "a tripped dashboard is not polled all evening");
});

test("a failure backs off rather than asking harder", async () => {
  const it = await live({});
  it.clear().settings.offline = true;
  await it.live.poll();
  await it.live.poll();
  assert.deepStrictEqual(it.delays, [120000, 240000]);
});

test("a tab nobody is looking at costs nothing", async () => {
  const it = await live({});
  Object.defineProperty(it.win.document, "visibilityState",
                        {value: "hidden", configurable: true});
  it.clear();
  await it.live.poll();
  assert.deepStrictEqual(it.asked, [], "no request from a hidden tab");
});

test("it asks straight away when a reader comes back to the tab", async () => {
  const it = await live({});
  const doc = it.win.document;
  Object.defineProperty(doc, "visibilityState",
                        {value: "hidden", configurable: true});
  doc.dispatchEvent(new it.win.Event("visibilitychange"));
  it.clear();

  Object.defineProperty(doc, "visibilityState",
                        {value: "visible", configurable: true});
  doc.dispatchEvent(new it.win.Event("visibilitychange"));
  await settle();
  assert.deepStrictEqual(it.asked, ["/api/status"],
                         "right by the time it is read, not a minute after");
});

test("an answer nobody is waiting on any more draws nothing", async () => {
  /* A poll left in flight when the tab was hidden used to come back and arm a
     timer in a stopped tab, and - worse - a page alt-tabbed at twice could
     have two in flight and be drawn by the older one. */
  const it = await live({stamp: "seen", reply: {body: status({stamp: "seen"})}});
  const stale = it.clear().live.poll();      // asked for by this reader...
  it.live.stop();                            // ...who then left the tab
  await stale;
  assert.deepStrictEqual(it.delays, [], "no timer armed in a stopped tab");
});

test("a page coming back from the browser cache asks again", async () => {
  /* Back and forward restore the page with its timers frozen and the figures
     it was left with, and fire no visibilitychange at all. */
  const it = await live({});
  it.clear();
  const event = new it.win.Event("pageshow");
  event.persisted = true;
  it.win.dispatchEvent(event);
  await settle();
  assert.deepStrictEqual(it.asked, ["/api/status"]);
});

test("the network coming back is not waited out", async () => {
  const it = await live({});
  it.clear().settings.offline = true;
  await it.live.poll();
  await it.live.poll();
  assert.deepStrictEqual(it.delays, [120000, 240000], "backed off while down");

  it.clear().settings.offline = false;
  it.win.dispatchEvent(new it.win.Event("online"));
  await settle();
  assert.deepStrictEqual(it.asked, ["/api/status"],
                         "the browser knows before the next gap is up");
  assert.deepStrictEqual(it.delays, [60000], "and the gap is a normal one again");
});
