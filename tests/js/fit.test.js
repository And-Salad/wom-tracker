/* fit.js: a .scroll box is a scroller only while it needs to be. */
"use strict";

const test = require("node:test");
const assert = require("node:assert");
const { page, measure, source } = require("./page");

const HTML = `<!doctype html><html><body>
  <div class="scroll" id="table"><table></table></div>
  <div class="scroll grid-scroll" id="grid"><table></table></div>
</body></html>`;

function laidOut(widths) {
  const dom = page(HTML, []);
  Object.keys(widths).forEach(function (id) {
    measure(dom.window.document.getElementById(id), widths[id][0], widths[id][1]);
  });
  dom.window.eval(source("fit.js"));
  return dom.window.document;
}

test("a box whose contents fit is marked as fitting", () => {
  const doc = laidOut({ table: [400, 400] });
  assert.ok(doc.getElementById("table").classList.contains("fits"));
});

test("a box that really is overflowing keeps its scroller", () => {
  const doc = laidOut({ table: [900, 400] });
  assert.ok(!doc.getElementById("table").classList.contains("fits"));
});

test("half a pixel of overflow is not overflow", () => {
  /* A table whose width lands on a fraction of a pixel is "overflowing" by
     an amount nobody can see, which looks like a hidden column. */
  const doc = laidOut({ table: [401, 400] });
  assert.ok(doc.getElementById("table").classList.contains("fits"));
});

test("the Data grid is left alone, because it scrolls on purpose", () => {
  const doc = laidOut({ table: [400, 400], grid: [400, 400] });
  assert.ok(!doc.getElementById("grid").classList.contains("fits"));
});

test("a box remeasured after it narrows becomes a scroller again", () => {
  const dom = page(HTML, []);
  const box = dom.window.document.getElementById("table");
  measure(box, 400, 400);
  dom.window.eval(source("fit.js"));
  assert.ok(box.classList.contains("fits"));

  measure(box, 900, 400);
  dom.window.dispatchEvent(new dom.window.Event("resize"));
  assert.ok(!box.classList.contains("fits"));
});

test("a browser with no MutationObserver still gets the first measurement", () => {
  const dom = page(HTML, []);
  measure(dom.window.document.getElementById("table"), 400, 400);
  dom.window.MutationObserver = undefined;
  assert.doesNotThrow(() => dom.window.eval(source("fit.js")));
  assert.ok(dom.window.document.getElementById("table").classList.contains("fits"));
});
