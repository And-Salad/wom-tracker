/* The milestones feed, redrawn whenever the sidebar changes.
 *
 * The server still renders the first copy, which is what a reader with no
 * JavaScript gets; this only replaces it in place afterwards.
 */
(function () {
  "use strict";

  var body = document.getElementById("feed");
  var count = document.getElementById("count");
  if (!body || !window.Sidebar) { return; }

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) { node.className = className; }
    if (text !== undefined) { node.textContent = text; }
    return node;
  }

  /* Built as nodes, never as an HTML string: a display name and a milestone
     name both come from the Wise Old Man API. */
  function rowNode(row) {
    var tr = el("tr");
    var icon = el("td");
    if (row.kind) {
      var img = el("img", "feed-icon");
      img.src = "/icon/" + encodeURIComponent(row.kind) + "/" +
        encodeURIComponent(row.metric) + ".png";
      img.alt = row.metric;
      img.title = row.metric;
      // A metric with no sprite on disk should leave a gap, not a broken icon.
      img.addEventListener("error", function () { img.remove(); });
      icon.appendChild(img);
    }
    tr.appendChild(icon);
    tr.appendChild(el("td", null, row.when));
    tr.appendChild(el("td", "dim", row.ago));
    var who = el("td", null, row.player);
    who.style.color = row.color;
    tr.appendChild(who);
    tr.appendChild(el("td", null, row.name));
    return tr;
  }

  function refill(feed) {
    body.textContent = "";
    if (!feed.length) {
      var empty = el("tr");
      var cell = el("td", "dim", "Nothing recorded for this selection.");
      cell.colSpan = 5;
      empty.appendChild(cell);
      body.appendChild(empty);
    } else {
      var frag = document.createDocumentFragment();
      feed.forEach(function (row) { frag.appendChild(rowNode(row)); });
      body.appendChild(frag);
    }
    count.textContent = feed.length +
      (feed.length === 1 ? " achievement" : " achievements") +
      ", newest first. A leading ~ means Wise Old Man only knows the date roughly.";
  }

  var seq = 0;

  window.Sidebar.onChange(function (query) {
    var mine = ++seq;
    fetch("/api/milestones?" + query)
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (mine !== seq) { return; }      // an older reply, now out of date
        window.Sidebar.showWindow(data.span);
        refill(data.feed || []);
      })
      .catch(function () { /* the feed on screen is still true */ });
  });
})();
