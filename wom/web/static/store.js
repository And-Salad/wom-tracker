/* What the reader chose, kept between visits.
 *
 * Every control on the site is a choice somebody made about what they wanted
 * to see, and every one of them was thrown away on the next visit: the
 * sidebar came back with all six accounts ticked over the last week, the
 * milestone kinds came back all on, and each Overview card came back on its
 * first metric. A tracker is a page people open every morning, so that is the
 * same handful of clicks every morning.
 *
 * Deliberately one small thing. Anything stored here is a convenience the
 * page must work without: a reader in a private window, one who has turned
 * site data off, or a browser that throws on the very first read still gets
 * the page the server rendered. So every call is wrapped, a failure is
 * silent, and a missing value is simply the default.
 *
 * Values are JSON under a "wom." prefix, so the keys are recognisable beside
 * whatever else an origin holds and cannot collide with it.
 */
(function () {
  "use strict";

  var PREFIX = "wom.";

  /* Not `!!window.localStorage`: reading the property itself throws in a
     browser set to block site data, which is exactly the case this is for. */
  function store() {
    try {
      return window.localStorage;
    } catch (e) {
      return null;
    }
  }

  function read(name, fallback) {
    var held = store();
    if (!held) { return fallback; }
    try {
      var raw = held.getItem(PREFIX + name);
      return raw === null ? fallback : JSON.parse(raw);
    } catch (e) {
      // Unreadable, so it is no use to anybody. Left in place rather than
      // deleted: another tab on a newer version may well understand it.
      return fallback;
    }
  }

  function write(name, value) {
    var held = store();
    if (!held) { return; }
    try {
      held.setItem(PREFIX + name, JSON.stringify(value));
    } catch (e) { /* full, or refused: the page is unaffected */ }
  }

  window.WOM = window.WOM || {};
  window.WOM.Remember = {read: read, write: write};
})();
