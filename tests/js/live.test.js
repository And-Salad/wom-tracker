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
    <span id="stat-players">2</span> players &middot;
    updated <span id="stat-last">3m ago</span> &middot;
    <span id="stat-next">next Wed 14:20</span>
  </span>
  <button type="button" class="link" id="refresh-now" hidden>New data</button>
</header></body></html>`;

/* What the server answers, in the shape selection.status() builds. */
function status(fields) {
  return Object.assign({players: 2, last: "just now", next: "Wed 14:30",
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
  let moved = 0;
  const real = win.Date.now;
  win.Date.now = function () { return real() + moved; };

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

function at(offsetMs) {
  return new Date(Date.now() + offsetMs).toISOString();
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
  assert.deepStrictEqual(it.delays, [15000],
                         "a run is owed, so the gap tightens to fifteen seconds");
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
