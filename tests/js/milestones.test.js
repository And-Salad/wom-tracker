/* milestones.js: the feed redrawn when the sidebar changes.
 *
 * The server renders the first copy and this replaces it in place, so the two
 * renderers have to agree about every column. They had drifted once already -
 * the icon column was dead on one side - which is what these check.
 */
"use strict";

const test = require("node:test");
const assert = require("node:assert");
const { page, source } = require("./page");

const HTML = `<!doctype html><html><body>
  <p class="hint" id="count"></p>
  <div class="row types" id="types">
    <label class="tick"><input type="checkbox" value="quest" checked></label>
    <label class="tick"><input type="checkbox" value="pet" checked></label>
  </div>
  <table><tbody id="feed"></tbody></table>
</body></html>`;

/* The script is an IIFE that wires itself to window.Sidebar the moment it
   runs, so both the page and the sidebar have to exist before it is eval'd.
   Everything it does afterwards goes through the callback it registers. */
function feedPage(rows, truncated) {
  const dom = page(HTML, []);
  const win = dom.window;
  let redraw = null;
  win.Sidebar = {
    onChange(fn) { redraw = fn; },
    showWindow() {},
  };
  win.fetch = () => Promise.resolve({
    json: () => Promise.resolve({ feed: rows, truncated: !!truncated,
                                  span: {} }),
  });
  win.eval(source("milestones.js"));
  return { dom, win, draw: async () => { redraw("period=Year"); await tick(); } };
}

/* Two turns of the microtask queue: the fetch promise, then its .json(). */
const tick = () => new Promise((done) => setTimeout(done, 0));

function row(over) {
  return Object.assign({
    at: "2026-09-03T21:15:00.000Z", when: "03 Sep 2026", ago: "1d ago",
    within: "", player: "Zezima", color: "#8ab4f8", name: "Dragon Slayer I",
    detail: "", category: "quest", metric: null, kind: null,
    source: "dink", precision: "exact",
  }, over || {});
}

test("a row carries the source it came from", async () => {
  const { win, draw } = feedPage([row(), row({ category: "milestone",
                                               source: "wom" })]);
  await draw();
  const got = [...win.document.querySelectorAll("#feed tr")]
    .map((tr) => tr.getAttribute("data-source"));
  assert.deepStrictEqual(got, ["dink", "wom"]);
});

test("a row with a metric gets the icon the server would have given it", async () => {
  const { win, draw } = feedPage([row({ metric: "collections_logged",
                                        kind: "boss" })]);
  await draw();
  const img = win.document.querySelector("#feed img.feed-icon");
  assert.ok(img, "a row with an icon kind must render one");
  assert.match(img.getAttribute("src"), /\/icon\/boss\/collections_logged\.png$/);
});

test("a row with no metric leaves the column empty, not broken", async () => {
  const { win, draw } = feedPage([row()]);
  await draw();
  assert.strictEqual(win.document.querySelector("#feed img"), null);
  assert.strictEqual(win.document.querySelectorAll("#feed td").length, 5,
                     "the empty cell still has to be there or the columns shift");
});

test("how rough a date is becomes the tooltip, and only when it is rough", async () => {
  const { win, draw } = feedPage([
    row({ when: "~03 Sep 2026", precision: "approximate",
          within: "Somewhere in a window of 74 days." }),
    row({ name: "Cook's Assistant" }),
  ]);
  await draw();
  const dates = [...win.document.querySelectorAll("#feed tr")]
    .map((tr) => tr.cells[1].title);
  assert.strictEqual(dates[0], "Somewhere in a window of 74 days.");
  assert.strictEqual(dates[1], "", "an exact date explains nothing");
});

test("a feed the server had to cut says so", async () => {
  const { win, draw } = feedPage([row()], true);
  await draw();
  assert.match(win.document.getElementById("count").textContent,
               /narrow the window/);
});

test("a feed that fit says nothing about narrowing anything", async () => {
  const { win, draw } = feedPage([row()], false);
  await draw();
  assert.doesNotMatch(win.document.getElementById("count").textContent,
                      /narrow the window/);
});

test("a pet row can be unticked, which it could not when it had no box", async () => {
  const { win, draw } = feedPage([row({ category: "pet", name: "Ikkle hydra" })]);
  await draw();
  const box = win.document.querySelector('input[value="pet"]');
  box.checked = false;
  box.dispatchEvent(new win.Event("change", { bubbles: true }));
  assert.strictEqual(win.document.querySelector("#feed tr").hidden, true);
});
