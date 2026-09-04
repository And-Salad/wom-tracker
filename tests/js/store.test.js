/* store.js: what the reader chose, kept between visits.
 *
 * The file's own comment promises that a reader in a private window, one who
 * has turned site data off, or a browser that throws on the very first read
 * still gets the page the server rendered. That promise is the whole design,
 * and nothing had ever checked it.
 */
"use strict";

const test = require("node:test");
const assert = require("node:assert");
const { page } = require("./page");

function remember(dom) {
  return dom.window.WOM.Remember;
}

test("a value written is the value read back", () => {
  const dom = page(null, ["store.js"]);
  remember(dom).write("period", "Week");
  assert.strictEqual(remember(dom).read("period", "Day"), "Week");
});

test("anything JSON can carry survives the round trip", () => {
  const dom = page(null, ["store.js"]);
  const chose = { players: ["zezima", "other"], kinds: { pet: true }, n: 3 };
  remember(dom).write("choice", chose);
  assert.deepStrictEqual(remember(dom).read("choice", null), chose);
});

test("a value nobody has written is the default", () => {
  const dom = page(null, ["store.js"]);
  assert.strictEqual(remember(dom).read("never-set", "Day"), "Day");
});

test("false and zero are values, not absences", () => {
  const dom = page(null, ["store.js"]);
  remember(dom).write("open", false);
  assert.strictEqual(remember(dom).read("open", true), false);
  remember(dom).write("count", 0);
  assert.strictEqual(remember(dom).read("count", 9), 0);
});

test("keys are namespaced, so they cannot collide with the origin's own", () => {
  const dom = page(null, ["store.js"]);
  remember(dom).write("period", "Week");
  assert.strictEqual(dom.window.localStorage.getItem("wom.period"), '"Week"');
  assert.strictEqual(dom.window.localStorage.getItem("period"), null);
});

test("unreadable stored text is the default, and is left in place", () => {
  /* Another tab on a newer version may well understand it. */
  const dom = page(null, ["store.js"]);
  dom.window.localStorage.setItem("wom.period", "{not json");
  assert.strictEqual(remember(dom).read("period", "Day"), "Day");
  assert.strictEqual(dom.window.localStorage.getItem("wom.period"), "{not json");
});

test("a browser that throws on the property itself is survived", () => {
  /* Not `!!window.localStorage`: reading it throws where site data is
     blocked, which is exactly the case the file is written for. */
  const dom = page(null, []);
  Object.defineProperty(dom.window, "localStorage", {
    configurable: true,
    get() { throw new Error("site data is blocked"); },
  });
  dom.window.eval(require("./page").source("store.js"));

  assert.strictEqual(remember(dom).read("period", "Day"), "Day");
  assert.doesNotThrow(() => remember(dom).write("period", "Week"));
});

test("a full or refused write is silent rather than fatal", () => {
  const dom = page(null, ["store.js"]);
  dom.window.localStorage.setItem = () => { throw new Error("quota"); };
  assert.doesNotThrow(() => remember(dom).write("period", "Week"));
  assert.strictEqual(remember(dom).read("period", "Day"), "Day");
});

test("the handle is shared rather than replaced", () => {
  /* Every other file reaches the page through window.WOM, and store.js is
     loaded before some of them and after others. */
  const dom = page(null, []);
  dom.window.eval("window.WOM = { Chart: 'already here' };");
  dom.window.eval(require("./page").source("store.js"));
  assert.strictEqual(dom.window.WOM.Chart, "already here");
  assert.ok(dom.window.WOM.Remember);
});
