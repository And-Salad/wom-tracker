/* fragments.js: the server-rendered parts of a page, brought up to date in
 * place - and live.js choosing that over offering a reload.
 */
"use strict";

const test = require("node:test");
const assert = require("node:assert");
const { page, source } = require("./page");

function settle() {
  return new Promise((resolve) => setTimeout(resolve, 0));
}

const BEFORE = `<!doctype html><html><body>
  <div data-fragment="calendar"><a class="day">1</a></div>
  <div data-fragment="recaps">
    <details data-key="mine"><summary>Mine 3h ago</summary><p>old</p></details>
  </div>
  <div data-fragment="steady"><span id="kept">same</span></div>
  <p id="outside">not a fragment</p>
</body></html>`;

const AFTER = `<!doctype html><html><body>
  <div data-fragment="calendar"><a class="day won">1</a></div>
  <div data-fragment="recaps">
    <details data-key="mine"><summary>Mine 4h ago</summary><p>new</p></details>
  </div>
  <div data-fragment="steady"><span id="kept">same</span></div>
  <p id="outside">changed outside</p>
</body></html>`;

function withServer(dom, html) {
  const asked = [];
  dom.window.fetch = (url, options) => {
    asked.push({url, options});
    return Promise.resolve({ok: true, text: () => Promise.resolve(html)});
  };
  return asked;
}

test("a refresh moves each marked part across, and nothing else", async () => {
  const dom = page(BEFORE, ["fragments.js"]);
  const asked = withServer(dom, AFTER);
  const doc = dom.window.document;
  const kept = doc.getElementById("kept");
  assert.strictEqual(dom.window.WOM.Fragments.refresh(), true);
  await settle();
  assert.strictEqual(asked.length, 1);
  assert.strictEqual(asked[0].url, dom.window.location.href,
                     "the same page, so the same template draws it");
  assert.ok(doc.querySelector(".day.won"));
  assert.match(doc.querySelector("[data-fragment=recaps] p").textContent, /new/);
  assert.strictEqual(doc.getElementById("outside").textContent, "not a fragment");
  assert.strictEqual(doc.getElementById("kept"), kept,
                     "an unchanged part is left alone, element and all");
});

test("a recap folded away stays folded", async () => {
  const dom = page(BEFORE, ["fragments.js"]);
  withServer(dom, AFTER.replace("<details", "<details open"));
  const doc = dom.window.document;
  doc.querySelector("details[data-key=mine]").open = false;
  dom.window.WOM.Fragments.refresh();
  await settle();
  assert.strictEqual(doc.querySelector("details[data-key=mine]").open, false);
});

test("a replaced part says so, for scripts that wired up what was in it",
     async () => {
  const dom = page(BEFORE, ["fragments.js"]);
  withServer(dom, AFTER);
  const heard = [];
  dom.window.document.addEventListener("wom:refreshed", (event) => {
    heard.push(event.target.getAttribute("data-fragment"));
  });
  dom.window.WOM.Fragments.refresh();
  await settle();
  assert.deepStrictEqual(heard.sort(), ["calendar", "recaps"]);
});

test("asking twice for the same page while it is coming is one fetch",
     async () => {
  const dom = page(BEFORE, ["fragments.js"]);
  const asked = withServer(dom, AFTER);
  dom.window.WOM.Fragments.refresh();
  dom.window.WOM.Fragments.refresh();
  await settle();
  await settle();
  assert.strictEqual(asked.length, 1);
});

test("a failed fetch leaves the page as it was", async () => {
  const dom = page(BEFORE, ["fragments.js"]);
  dom.window.fetch = () => Promise.resolve({ok: false, status: 429});
  dom.window.WOM.Fragments.refresh();
  await settle();
  assert.ok(dom.window.document.querySelector(".day:not(.won)"));
});

test("a page with nothing marked says it could not", () => {
  const dom = page("<!doctype html><html><body></body></html>", ["fragments.js"]);
  assert.strictEqual(dom.window.WOM.Fragments.refresh(), false);
});

const HEADER = `<!doctype html><html><body><header>
  <span class="status" id="freshness" data-stamp="old" data-next="">
    <span id="stat-viewers"></span><span id="stat-last"></span>
    <span id="stat-next"></span></span>
  <button type="button" id="refresh-now" hidden>New data</button>
</header>BODY</body></html>`;

async function landed(body, sidebarRefetches) {
  const dom = page(HEADER.replace("BODY", body), []);
  const win = dom.window;
  const fetched = [];
  win.fetch = (url) => {
    fetched.push(url);
    if (url === "/api/status") {
      return Promise.resolve({ok: true, json: () => Promise.resolve(
        {stamp: "new", last: "just now", viewers: 1})});
    }
    return Promise.resolve({ok: true, text: () => Promise.resolve(
      HEADER.replace("BODY", body))});
  };
  if (sidebarRefetches !== undefined) {
    win.Sidebar = {refresh: () => sidebarRefetches};
  }
  win.eval(source("fragments.js"));
  win.eval(source("live.js"));
  await settle();
  await settle();
  // Its timers would otherwise keep the test process alive for ever.
  win.WOM.Live.stop();
  const offered = !win.document.getElementById("refresh-now").hidden;
  win.close();
  return {offered, fetched};
}

test("a page with server-rendered parts refreshes them rather than offering " +
     "a reload", async () => {
  const {offered, fetched} = await landed(
    '<div data-fragment="calendar"></div>', false);
  assert.strictEqual(offered, false);
  assert.ok(fetched.some((url) => url !== "/api/status"));
});

test("a chart that redraws itself no longer hides a calendar that does not",
     async () => {
  /* Leaderboards: the sidebar has a listener, so it said "refreshed", and
     the calendar beside the chart was never asked about. */
  const {fetched} = await landed('<div data-fragment="calendar"></div>', true);
  assert.ok(fetched.some((url) => url !== "/api/status"),
            "the calendar was fetched as well as the chart");
});

test("a page with neither is still offered the reload", async () => {
  const {offered} = await landed("<p>plain</p>", false);
  assert.strictEqual(offered, true);
});
