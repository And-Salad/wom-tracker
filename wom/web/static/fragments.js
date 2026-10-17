/* Refreshing the parts of a page the server rendered, without a reload.
 *
 * Most of what changes on this site is fetched as JSON and redrawn by the
 * page's own script. The rest - the leaderboard calendar and standings, the
 * recaps, the gallery, the admin page's session table - is rendered by the
 * server, and the only way it ever caught up was a reload: offered behind a
 * button on some pages, and on Leaderboards not offered at all, because the
 * chart there refreshed itself and that was taken to mean the page had.
 *
 * So each such part is marked `data-fragment="name"`, and refreshing asks the
 * server for the same page again and moves each part across. The same page,
 * not an endpoint per part: the template that drew it first is the one that
 * draws it again, so the two cannot drift apart, and the page's own address
 * carries every choice it was drawn for - the board, the day, the ticks.
 *
 * Only what differs is replaced, and a <details data-key> keeps whether it
 * was open, so a recap being read stays where the reader left it.
 * Every replaced part announces `wom:refreshed`, for a page script that had
 * wired up what was inside it to do so again.
 */
(function () {
  "use strict";

  var busy = "";        // the address a refresh in flight asked for
  var again = false;    // and a different one asked for while it was

  function parts() {
    return document.querySelectorAll("[data-fragment]");
  }

  function openness(root) {
    var open = Object.create(null);
    Array.prototype.forEach.call(root.querySelectorAll("details[data-key]"),
      function (node) { open[node.getAttribute("data-key")] = node.open; });
    return open;
  }

  function restore(root, open) {
    Array.prototype.forEach.call(root.querySelectorAll("details[data-key]"),
      function (node) {
        var key = node.getAttribute("data-key");
        if (key in open) { node.open = open[key]; }
      });
  }

  function swap(html) {
    var fresh = new DOMParser().parseFromString(html, "text/html");
    var found = Object.create(null);
    Array.prototype.forEach.call(fresh.querySelectorAll("[data-fragment]"),
      function (node) { found[node.getAttribute("data-fragment")] = node; });
    Array.prototype.forEach.call(parts(), function (part) {
      var replacement = found[part.getAttribute("data-fragment")];
      if (!replacement || replacement.innerHTML === part.innerHTML) { return; }
      var open = openness(part);
      part.innerHTML = replacement.innerHTML;
      restore(part, open);
      part.dispatchEvent(new CustomEvent("wom:refreshed", {bubbles: true}));
    });
  }

  /* Ask for the page again and bring every marked part up to date. Answers
     whether the page had any - a page with none needs whoever asked to find
     another way. A failure leaves what is on screen alone: a stale table
     beats one replaced by an error. */
  function refresh() {
    if (!parts().length || !window.fetch || !window.DOMParser) { return false; }
    /* The same address already on its way is the same answer: a run landing
       and a page script asking at once is one fetch, not two. A different
       one - the ticks moved meanwhile - is asked for when it is back. */
    if (busy) {
      again = again || busy !== window.location.href;
      return true;
    }
    busy = window.location.href;
    fetch(busy, {cache: "no-store", credentials: "same-origin",
                 headers: {Accept: "text/html"}})
      .then(function (response) {
        if (!response.ok) { throw new Error("HTTP " + response.status); }
        return response.text();
      })
      .then(swap)
      .catch(function () { /* keep what is shown */ })
      .then(function () {
        busy = "";
        if (again) {
          again = false;
          refresh();
        }
      });
    return true;
  }

  window.WOM = window.WOM || {};
  window.WOM.Fragments = {refresh: refresh};
})();
