/* The milestones feed, redrawn whenever the sidebar changes.
 *
 * The server still renders the first copy, which is what a reader with no
 * JavaScript gets; this only replaces it in place afterwards.
 */
(function () {
  "use strict";

  var body = document.getElementById("feed");
  var count = document.getElementById("count");
  var types = document.getElementById("types");
  if (!body || !window.Sidebar) { return; }

  /* Which kinds are showing. Absent from the set means hidden, so a kind the
     server sends that this does not know about stays visible.

     Kept between visits: a reader who only cares about 99s should not have to
     untick four boxes every time they open the page. Stored as the kinds
     turned *off*, so a category added later starts on, the same as it does
     for somebody who has never touched this. */
  var HIDDEN = "milestones.hidden";
  var remember = (window.WOM && window.WOM.Remember) ||
    {read: function (_n, fallback) { return fallback; }, write: function () {}};

  var hidden = Object.create(null);
  var stored = remember.read(HIDDEN, []);
  if (Array.isArray(stored)) {
    stored.forEach(function (kind) { hidden[kind] = true; });
  }

  function applyFilter() {
    var shown = 0;
    Array.prototype.forEach.call(body.rows, function (row) {
      var kind = row.getAttribute("data-category");
      var off = kind && hidden[kind];
      row.hidden = !!off;
      if (!off && kind) { shown += 1; }
    });
    return shown;
  }

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
    if (row.category) { tr.setAttribute("data-category", row.category); }
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
    var who = el("td", "named", row.player);
    who.style.setProperty("--dot", row.color);
    tr.appendChild(who);
    var name = el("td", null, row.name);
    if (row.detail) {
      name.appendChild(document.createTextNode(" "));
      name.appendChild(el("span", "dim", row.detail));
    }
    tr.appendChild(name);
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
    say(applyFilter(), feed.length);
  }

  function say(shown, total) {
    var text = shown + (shown === 1 ? " milestone" : " milestones");
    if (total !== undefined && total !== shown) {
      text += " of " + total;
    }
    count.textContent = text +
      ", newest first. A ~ marks a date Wise Old Man knows only roughly.";
  }

  if (types) {
    // The boxes are rendered checked, so the stored ones have to be unticked
    // before the first filter runs or the ticks would say the opposite of
    // what the feed shows.
    Array.prototype.forEach.call(
      types.querySelectorAll("input[type=checkbox]"),
      function (box) { if (hidden[box.value]) { box.checked = false; } });

    types.addEventListener("change", function (event) {
      var box = event.target;
      if (!box || box.type !== "checkbox") { return; }
      hidden[box.value] = !box.checked;
      remember.write(HIDDEN, Object.keys(hidden).filter(function (kind) {
        return hidden[kind];
      }));
      say(applyFilter(), body.rows.length);
    });
    // Only when something is actually hidden: with nothing stored the count
    // the server rendered is already right, and rewriting it would be a
    // flicker for every reader who has never touched these.
    if (Object.keys(hidden).length) {
      say(applyFilter(), body.rows.length);
    }
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
