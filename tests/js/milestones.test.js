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

function html(rendered, total, truncated) {
  return `<!doctype html><html><body>
  <p class="hint" id="count" data-total="${total || 0}"${truncated ? " data-truncated" : ""}></p>
  <div class="row types" id="types">
    <label class="tick"><input type="checkbox" value="quest" checked></label>
    <label class="tick"><input type="checkbox" value="pet" checked></label>
  </div>
  <table><tbody id="feed">${(rendered || []).map(
    () => '<tr data-category="quest"><td></td><td></td><td></td><td></td><td></td></tr>'
  ).join("")}</tbody></table>
  <div class="row more" id="more"></div>
</body></html>`;
}

/* The script is an IIFE that wires itself to window.Sidebar the moment it
   runs, so both the page and the sidebar have to exist before it is eval'd.
   Everything it does afterwards goes through the callback it registers.

   `answers` is a list of payloads, one per fetch, so a test can say what the
   second reply looks like as well as the first. */
function feedPage(rows, truncated, options) {
  const opts = options || {};
  const total = opts.total === undefined
    ? (rows.length + (truncated ? 1 : 0)) : opts.total;
  const dom = page(html(opts.rendered, total, truncated), []);
  const win = dom.window;
  const asked = [];
  let redraw = null;
  let answers = opts.answers || [{ feed: rows, truncated: !!truncated,
                                  total, page: opts.page || 100, span: {} }];
  win.Sidebar = { onChange(fn) { redraw = fn; }, showWindow() {} };
  win.fetch = (url) => {
    asked.push(url);
    const next = answers.length > 1 ? answers.shift() : answers[0];
    return opts.fail
      ? Promise.reject(new Error("offline"))
      : Promise.resolve({ json: () => Promise.resolve(next) });
  };
  win.eval(source("milestones.js"));
  return {
    dom, win, asked,
    draw: async (query) => { redraw(query || "period=Year"); await tick(); },
    click: async () => {
      win.document.querySelector("#more button").dispatchEvent(
        new win.Event("click", { bubbles: true }));
      await tick();
    },
  };
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

test("the count says how much of what is loaded is on screen", async () => {
  const { win, draw } = feedPage([row(), row({ category: "pet" })], false);
  await draw();
  const box = win.document.querySelector('input[value="pet"]');
  box.checked = false;
  box.dispatchEvent(new win.Event("change", { bubbles: true }));
  assert.match(win.document.getElementById("count").textContent,
               /^1 milestone of 2,/);
});

test("the advice to narrow the window is left to readers with no script", async () => {
  /* It is the server's sentence, and the right one when nothing can load
     more. With script there is a button under the table saying the same
     thing better, so the line must not offer both. */
  const { win, draw } = feedPage([row()], true);
  await draw();
  assert.doesNotMatch(win.document.getElementById("count").textContent,
                      /narrow the window/);
  assert.ok(win.document.querySelector("#more button"));
});

test("a pet row can be unticked, which it could not when it had no box", async () => {
  const { win, draw } = feedPage([row({ category: "pet", name: "Ikkle hydra" })]);
  await draw();
  const box = win.document.querySelector('input[value="pet"]');
  box.checked = false;
  box.dispatchEvent(new win.Event("change", { bubbles: true }));
  assert.strictEqual(win.document.querySelector("#feed tr").hidden, true);
});

/* -- load more ----------------------------------------------------------- */

test("a feed that fits offers no button at all", async () => {
  const { win, draw } = feedPage([row()], false);
  await draw();
  assert.strictEqual(win.document.querySelector("#more button"), null);
});

test("a button says how many are still behind it", async () => {
  const { win, draw } = feedPage([row(), row()], true, { total: 847 });
  await draw();
  assert.strictEqual(win.document.querySelector("#more button").textContent,
                     "Load more (845 left)");
});

test("the server's rendered page is offered a button before any fetch", async () => {
  /* The first copy of the feed comes from the server, so a reader who has
     not touched the sidebar still has to be able to load more. */
  const { win } = feedPage([], true, { rendered: [1, 2], total: 9 });
  assert.strictEqual(win.document.querySelector("#more button").textContent,
                     "Load more (7 left)");
});

test("load more asks for a longer list, not for the next page", async () => {
  const { asked, draw, click } = feedPage([row(), row()], true, {
    total: 500, page: 100,
  });
  await draw();
  await click();
  assert.match(asked[0], /limit=100$/);
  assert.match(asked[1], /limit=102$/, "two loaded plus a page");
});

test("the page size comes from the server, not from a copy of the number", async () => {
  const { asked, draw, click } = feedPage([row()], true, {
    total: 500, page: 25,
  });
  await draw();
  await click();
  assert.match(asked[1], /limit=26$/, "one loaded plus the server's page");
});

test("changing the window starts again at one page", async () => {
  const { asked, draw, click } = feedPage([row()], true, { total: 500 });
  await draw("period=Year");
  await click();
  await draw("period=Week");
  assert.match(asked[asked.length - 1], /limit=100$/);
});

test("a refresh behind a reader's back keeps the length they had loaded",
     async () => {
       /* When an update lands, live.js asks these same listeners for the same
          view again. The reset above is keyed on the query changing, which is
          what keeps the two apart - a run finishing must not collapse a feed
          somebody has loaded four pages of back to the first one. */
       const { asked, draw, click } = feedPage([row()], true, { total: 500 });
       await draw("period=Year");
       await click();
       await draw("period=Year");        // the same view again, not a new one
       assert.match(asked[asked.length - 1], /limit=101$/,
                    "the length they were reading, asked for again");
     });

test("a load that fails puts the button back rather than leaving it busy", async () => {
  const { win, draw, click } = feedPage([row()], true, { total: 9 });
  await draw();
  win.fetch = () => Promise.reject(new Error("offline"));
  await click();
  const button = win.document.querySelector("#more button");
  assert.strictEqual(button.disabled, false);
  assert.match(button.textContent, /Load more/);
});

test("the button goes away once the whole list is loaded", async () => {
  const { win, draw, click } = feedPage(null, true, {
    total: 3,
    answers: [
      { feed: [row(), row()], truncated: true, total: 3, page: 100, span: {} },
      { feed: [row(), row(), row()], truncated: false, total: 3, page: 100,
        span: {} },
    ],
  });
  await draw();
  assert.ok(win.document.querySelector("#more button"));
  await click();
  assert.strictEqual(win.document.querySelector("#more button"), null);
  assert.strictEqual(win.document.querySelectorAll("#feed tr").length, 3);
});

test("a button that cannot go further stops offering to", async () => {
  /* The server caps what one request may carry. Past that the answer stops
     growing, and a button still saying how many are left is a button that
     does nothing when you press it. */
  const capped = { feed: [row(), row()], truncated: true, total: 900,
                   page: 100, span: {} };
  const { win, draw, click } = feedPage(null, true, {
    total: 900, answers: [capped, capped],
  });
  await draw();
  assert.ok(win.document.querySelector("#more button"));
  await click();
  assert.strictEqual(win.document.querySelector("#more button"), null);
  assert.match(win.document.getElementById("more").textContent,
               /as much as one request will carry/);
});

test("a shorter list from a new window is not a stall", async () => {
  /* Picking a narrower period legitimately returns fewer rows. That is a
     different list, not the end of this one. */
  const { win, draw } = feedPage(null, true, {
    total: 900,
    answers: [
      { feed: [row(), row(), row()], truncated: true, total: 900, page: 100,
        span: {} },
      { feed: [row()], truncated: true, total: 400, page: 100, span: {} },
    ],
  });
  await draw("period=Year");
  await draw("period=Week");
  assert.ok(win.document.querySelector("#more button"),
            "a narrower window still has more behind it");
});
