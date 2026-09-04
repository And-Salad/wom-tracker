/* A page with our scripts on it, in jsdom.
 *
 * The static files are plain IIFEs that hang what they expose off window.WOM,
 * loaded by a <script> tag - so a test loads them the same way rather than
 * asking them to be modules they are not. Nothing in wom/web/static/ changed
 * to make this possible.
 */
"use strict";

const fs = require("fs");
const path = require("path");
const { JSDOM } = require("jsdom");

const STATIC = path.join(__dirname, "..", "..", "wom", "web", "static");

function source(name) {
  return fs.readFileSync(path.join(STATIC, name), "utf8");
}

/* `url` matters: jsdom refuses localStorage on an opaque origin, which is
   about:blank by default. */
function page(html, scripts, options) {
  const dom = new JSDOM(html || "<!doctype html><html><body></body></html>", {
    url: (options && options.url) || "https://tracker.example/",
    runScripts: "outside-only",
    pretendToBeVisual: true,
  });
  (scripts || []).forEach(function (name) {
    dom.window.eval(source(name));
  });
  return dom;
}

/* jsdom lays nothing out, so every element measures zero and a box can never
   be found to be overflowing. Tests that are about that measurement say what
   the two numbers are. */
function measure(element, scrollWidth, clientWidth) {
  Object.defineProperty(element, "scrollWidth", { value: scrollWidth,
                                                  configurable: true });
  Object.defineProperty(element, "clientWidth", { value: clientWidth,
                                                  configurable: true });
}

module.exports = { page, source, measure, STATIC };
