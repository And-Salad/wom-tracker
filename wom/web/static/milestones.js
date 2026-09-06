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
  var more = document.getElementById("more");
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
    if (row.source) { tr.setAttribute("data-source", row.source); }
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
    var when = el("td", null, row.when);
    // How wide the estimate behind a ~ actually is. Only rough dates carry
    // one, so an exact date gets no tooltip rather than an empty one.
    if (row.within) { when.title = row.within; }
    tr.appendChild(when);
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

  function refill(feed, truncated, total) {
    cut = !!truncated;
    if (total !== undefined) { held = total; }
    // A load that asked for more and got no more has hit the ceiling the
    // server puts on one request. There is no further to go, so the button
    // has to stop offering to go there.
    stalled = cut && loaded > 0 && feed.length <= loaded;
    loaded = feed.length;
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
    offerMore();
  }

  /* What the server said about the list this page is a slice of. Only it
     can know how many there are; the browser knows what it was sent. */
  var cut = count.hasAttribute("data-truncated");
  var held = parseInt(count.getAttribute("data-total"), 10) || 0;
  var loaded = body.rows.length;
  var stalled = false;

  /* How long a list to ask for next. Load more asks for the whole feed one
     page longer rather than for the next page: the two sources tie heavily
     on date - Wise Old Man stamps a whole snapshot gap with one instant -
     and a cursor through ties that dense is where rows go missing. The
     server sends the size it is using, so this is only the opening guess. */
  var PAGE = 100;
  var asked = PAGE;

  function offerMore() {
    if (!more) { return; }
    more.textContent = "";
    if (!cut) { return; }
    if (stalled) {
      more.appendChild(el("p", "hint",
        "That is as much as one request will carry. Narrow the window to "
        + "see the rest."));
      return;
    }
    var left = held - loaded;
    var button = el("button", "load-more",
                    left > 0 ? "Load more (" + left + " left)" : "Load more");
    button.type = "button";
    button.addEventListener("click", function () {
      button.disabled = true;
      button.textContent = "Loading…";
      asked = loaded + PAGE;
      load(latest);
    });
    more.appendChild(button);
  }

  function say(shown, total) {
    var text = shown + (shown === 1 ? " milestone" : " milestones");
    if (total !== undefined && total !== shown) {
      text += " of " + total;
    }
    // No advice about narrowing the window here, unlike the sentence the
    // server rendered: with script there is a button under the table, and
    // the button is where the rest of the list is.
    count.textContent =
      text + ", newest first. A ~ marks a date Wise Old Man knows only roughly.";
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
  var latest = "";

  function load(query) {
    var mine = ++seq;
    fetch("/api/milestones?" + query + "&limit=" + asked)
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (mine !== seq) { return; }      // an older reply, now out of date
        if (data.page) { PAGE = data.page; }
        window.Sidebar.showWindow(data.span);
        refill(data.feed || [], data.truncated, data.total);
      })
      .catch(function () {
        // The feed on screen is still true. Only the button lied about being
        // busy, so put it back the way it was rather than leaving it there
        // saying Loading at somebody whose connection dropped.
        if (mine === seq) { offerMore(); }
      });
  }

  window.Sidebar.onChange(function (query) {
    // A different window or a different set of players is a different list,
    // so it opens at one page again rather than at however far the last one
    // had been loaded.
    if (query !== latest) {
      // A different list, not a longer one: it opens at one page again, and
      // it has not stalled just because it is shorter than the last one.
      asked = PAGE;
      loaded = 0;
      stalled = false;
    }
    latest = query;
    load(query);
  });

  // The sentence the server rendered tells a reader with no script to narrow
  // the window instead. There is script, so say it with a button.
  if (cut) {
    say(applyFilter(), loaded);
    offerMore();
  }
})();
