/* chartkit.js: the escaping the tooltips depend on, and the handle it hangs
 * off the page.
 *
 * The tooltip is assembled as HTML, and a display name arrives from the Wise
 * Old Man API - so escapeHtml is the one thing standing between a name
 * carrying markup and that markup running. It is exported, so it can be
 * tested; the drawing needs a laid-out SVG and is not attempted here.
 */
"use strict";

const test = require("node:test");
const assert = require("node:assert");
const { page } = require("./page");

function kit() {
  const dom = page(null, ["d3.v7.min.js", "chartkit.js"]);
  return dom.window.WOM;
}

test("the page gets a Chart, a hideTip and an escapeHtml", () => {
  const wom = kit();
  assert.strictEqual(typeof wom.Chart, "function");
  assert.strictEqual(typeof wom.hideTip, "function");
  assert.strictEqual(typeof wom.escapeHtml, "function");
});

test("a display name carrying markup cannot run", () => {
  const escape = kit().escapeHtml;
  assert.strictEqual(escape("<script>alert(1)</script>"),
                     "&lt;script&gt;alert(1)&lt;/script&gt;");
});

test("every character that can break out of an attribute is escaped", () => {
  const escape = kit().escapeHtml;
  assert.strictEqual(escape(`&<>"'`), "&amp;&lt;&gt;&quot;&#39;");
});

test("the ampersand is escaped first, so nothing is double-decoded", () => {
  const escape = kit().escapeHtml;
  assert.strictEqual(escape("&lt;"), "&amp;lt;");
});

test("an ordinary name is left exactly as it is", () => {
  const escape = kit().escapeHtml;
  assert.strictEqual(escape("Zezima"), "Zezima");
  assert.strictEqual(escape("Iron Mammal"), "Iron Mammal");
});

test("something that is not a string is not an exception", () => {
  const escape = kit().escapeHtml;
  assert.strictEqual(escape(1234), "1234");
  assert.strictEqual(escape(null), "null");
  assert.strictEqual(escape(undefined), "undefined");
});

test("loading chartkit does not disturb a handle store.js already set", () => {
  const dom = page(null, ["store.js", "d3.v7.min.js", "chartkit.js"]);
  assert.ok(dom.window.WOM.Remember, "the reader's choices are still there");
  assert.ok(dom.window.WOM.Chart);
});
