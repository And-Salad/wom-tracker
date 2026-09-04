/* The Data page: one row per player per metric, sorted and filtered here.
 *
 * The server hands over the whole set for the ticked players once, and every
 * column click, search and filter after that is local. The alternative - a
 * round trip per sort - would put a database query behind a mouse click on a
 * machine that also runs the update schedule.
 */
(function () {
  "use strict";

  var full = new Intl.NumberFormat();
  var rows = [];                      // everything the server sent
  var sortKey = "gained";
  var sortDesc = true;
  var seq = 0;                        // an older reply may not overwrite a newer

  var body = document.querySelector("#table tbody");
  var state = document.getElementById("state");
  var count = document.getElementById("count");

  function text(value) {
    var node = document.createTextNode(value == null ? "" : String(value));
    return node;
  }

  /* Cells are built as nodes, never as an HTML string: a display name comes
     from the Wise Old Man API and would otherwise be markup in the page. */
  function cell(tr, value, cls) {
    var td = document.createElement("td");
    if (cls) { td.className = cls; }
    td.appendChild(text(value));
    tr.appendChild(td);
    return td;
  }

  function num(value) {
    return value == null || value === "" ? "" : full.format(value);
  }

  // -- the filters ---------------------------------------------------------

  var kindBox = document.getElementById("kind");
  var metricBox = document.getElementById("metric");

  /* Who is on the page is the sidebar's business, not this toolbar's. It is
     already a checkbox per player, it already decides which lines the chart
     draws, and a second control that could disagree with it only made it
     possible to filter the table to one player while the chart showed six. */
  function matching() {
    var kind = kindBox.value;
    var metric = metricBox.value;
    return rows.filter(function (row) {
      return row.kind === kind && (!metric || row.metric === metric);
    });
  }

  function fill(select, options, keep) {
    select.textContent = "";
    var has = options.some(function (o) { return o.value === keep; });
    options.forEach(function (option) {
      var node = document.createElement("option");
      node.value = option.value;
      node.textContent = option.label;
      select.appendChild(node);
    });
    select.value = has ? keep : options[0].value;
  }

  /* Metrics belong to a kind - there is no Zulrah among the skills - so the
     list is rebuilt whenever the kind changes rather than offering names
     that would match nothing.

     There is no "all metrics" here. One metric at a time is what makes the
     table a row per player instead of a hundred, and it is what the chart
     below it plots: a line per player of one thing is a chart, and a line
     per player per metric is a mess. */
  function metricOptions(kind) {
    var seen = {};
    var out = [];
    rows.forEach(function (row) {
      if (row.kind !== kind) { return; }
      if (!seen[row.metric]) {
        seen[row.metric] = {value: row.metric, label: row.label, moved: 0};
        out.push(seen[row.metric]);
      }
      seen[row.metric].moved += row.gained || 0;
    });
    out.sort(function (a, b) { return a.label.localeCompare(b.label); });
    return out;
  }

  /* What to show for a kind nobody has chosen a metric in yet: whatever moved
     most. Landing on "Abyssal Sire, all zeroes" because it sorts first tells
     the viewer nothing about the accounts in front of them. */
  function busiest(options) {
    if (!options.length) { return ""; }
    var best = options[0];
    options.forEach(function (option) {
      if (option.moved > best.moved) { best = option; }
    });
    return best.value;
  }

  function kindName(kind) {
    var option = kindBox.querySelector('option[value="' + kind + '"]');
    return option ? option.textContent.toLowerCase() : "metrics";
  }

  // Skills, and within them the one line that sums the rest: the page opens
  // on six rows saying where each account stands, not 666 saying everything.
  var firstMetric = "overall";

  function refreshChoices() {
    setMetrics(metricBox.value || firstMetric);
    firstMetric = null;       // only the first load gets the opening default
  }

  function setMetrics(keep) {
    var options = metricOptions(kindBox.value);
    if (!options.length) { metricBox.textContent = ""; return; }
    var has = options.some(function (o) { return o.value === keep; });
    fill(metricBox, options, has ? keep : busiest(options));
  }

  /* Missing is not small. A blank rank sorts to the end either way round,
     rather than pretending to be zero and leading an ascending sort. */
  function compare(a, b) {
    var x = a[sortKey], y = b[sortKey];
    var xNull = x === null || x === undefined || x === "";
    var yNull = y === null || y === undefined || y === "";
    if (xNull && yNull) { return 0; }
    if (xNull) { return 1; }
    if (yNull) { return -1; }
    var order;
    if (typeof x === "number" && typeof y === "number") { order = x - y; }
    else { order = String(x).localeCompare(String(y)); }
    if (sortDesc) { order = -order; }
    // A stable second key, so rows do not shuffle between equal values.
    return order || a.player.localeCompare(b.player) ||
           a.label.localeCompare(b.label);
  }

  function render() {
    var shown = matching().slice().sort(compare);
    body.textContent = "";
    var frag = document.createDocumentFragment();
    shown.forEach(function (row) {
      var tr = document.createElement("tr");
      if (row.gained) { tr.className = "moved"; }
      var name = cell(tr, "", "name");
      var dot = document.createElement("span");
      dot.className = "swatch";
      dot.style.setProperty("--dot", row.color);
      name.insertBefore(dot, name.firstChild);
      name.appendChild(text(row.player));
      cell(tr, row.label, "metric");
      cell(tr, row.level == null ? "" : row.level, "num dim wide-only");
      cell(tr, num(row.value), "num");
      cell(tr, row.gained ? "+" + num(row.gained) : "", "num gain");
      cell(tr, num(row.rank), "num dim wide-only");
      frag.appendChild(tr);
    });
    body.appendChild(frag);

    // Counted against the chosen kind, not the whole set: "6 of 666" when
    // 522 of those are bosses you did not ask for is a misleading fraction.
    var inKind = rows.filter(function (r) { return r.kind === kindBox.value; }).length;
    count.textContent = shown.length === inKind
      ? full.format(inKind) + " rows"
      : full.format(shown.length) + " of " + full.format(inKind) + " " +
        kindName(kindBox.value);
    state.textContent = shown.length ? "" :
      (rows.length ? "Nothing matches those filters."
                   : "No readings for the included players.");
    state.style.display = shown.length ? "none" : "";

    document.querySelectorAll("#table th[data-sort]").forEach(function (th) {
      var on = th.dataset.sort === sortKey;
      th.classList.toggle("sorted", on);
      th.classList.toggle("desc", on && sortDesc);
      th.setAttribute("aria-sort", on ? (sortDesc ? "descending" : "ascending")
                                      : "none");
    });
  }

  // -- talking to the server -----------------------------------------------

  /* Who is included and over what window are the sidebar's to decide, on this
     page as on every other. This one only says what to do when they change. */
  var queued = null;

  function load(query) {
    clearTimeout(queued);
    queued = setTimeout(function () {
      var mine = ++seq;
      exportTargets();
      state.style.display = "";
      state.textContent = "Loading…";
      fetch("/api/table?" + query).then(function (r) {
        if (!r.ok) { throw new Error(r.status === 429 ? "too many requests"
                                                      : "HTTP " + r.status); }
        return r.json();
      }).then(function (payload) {
        if (mine !== seq) { return; }
        rows = payload.rows || [];
        if (payload.span) { window.Sidebar.showWindow(payload.span); }
        if (payload.empty) {
          body.textContent = "";
          count.textContent = "";
          state.textContent = payload.empty;
          trend.message(payload.empty);
          return;
        }
        refreshChoices();
        render();
        drawHistory();
      }).catch(function (err) {
        if (mine !== seq) { return; }
        body.textContent = "";
        count.textContent = "";
        state.textContent = "Could not load the table: " + err.message;
      });
    }, 90);
  }

  // -- the chart below the table -------------------------------------------

  /* The table says where each account ended the window; the chart says how
     they got there. It is the same card machinery the Overview uses - only
     the endpoint differs, because this one is asked for whichever metric the
     filters are pointing at rather than for a fixed one. */
  var trend = new window.WOM.Chart(document.getElementById("history"));
  trend.endpoint = function (query) {
    return "/api/history?" + query +
      "&kind=" + encodeURIComponent(kindBox.value) +
      "&metric=" + encodeURIComponent(metricBox.value);
  };

  function drawHistory() {
    var option = metricBox.options[metricBox.selectedIndex];
    document.getElementById("history-title").textContent =
      (option ? option.textContent : "That metric") + " over the window";
    if (!metricBox.value) { return; }
    trend.load(window.Sidebar.query());
  }

  // -- the export dialog ---------------------------------------------------

  var dialog = document.getElementById("export-dialog");
  var form = document.getElementById("export");

  /* The dialog has no player picker of its own: the sidebar already decides
     who the page is about, and two lists of the same six names disagreeing
     with each other is how the wrong file gets downloaded. */
  function exportTargets() {
    // The export builds its own UTC bounds from the viewer's days, same as
    // the sidebar does; it just has its own field to say so in.
    var tz = document.getElementById("export-tzoffset");
    if (tz) { tz.value = String(-new Date().getTimezoneOffset()); }
    var host = document.getElementById("player-inputs");
    var picked = [];
    host.textContent = "";
    document.querySelectorAll(".side input[name=player]:checked")
      .forEach(function (box) {
        picked.push(box.parentNode.textContent.trim());
        var input = document.createElement("input");
        input.type = "hidden";
        input.name = "player";
        input.value = box.value;
        host.appendChild(input);
      });
    var who = document.getElementById("who");
    who.textContent = picked.length
      ? (picked.length === 1 ? picked[0]
         : picked.length + " players ticked in the sidebar")
      : "no players - tick at least one in the sidebar";
    estimate(picked.length);
  }

  function estimate(players) {
    var kinds = form.querySelectorAll("input[name=kind]:checked").length;
    var out = document.getElementById("estimate");
    if (!players) { out.textContent = "Pick at least one player in the sidebar."; return; }
    if (!kinds) { out.textContent = "Pick at least one kind of metric."; return; }
    out.textContent = players + (players === 1 ? " player" : " players") +
      " across " + kinds + (kinds === 1 ? " kind" : " kinds") + " of metric.";
  }

  document.addEventListener("DOMContentLoaded", function () {
    document.getElementById("open-export").addEventListener("click", function () {
      exportTargets();
      if (dialog.showModal) { dialog.showModal(); } else { dialog.open = true; }
    });
    document.getElementById("close-export").addEventListener("click", function () {
      dialog.close();
    });
    // Clicking the backdrop is outside the form but inside the dialog element.
    dialog.addEventListener("click", function (event) {
      if (event.target === dialog) { dialog.close(); }
    });
    form.addEventListener("change", function () {
      estimate(document.querySelectorAll("#player-inputs input").length);
    });
    form.addEventListener("submit", function () {
      // The download replaces nothing on the page, so put the dialog away.
      setTimeout(function () { dialog.close(); }, 0);
    });

    document.querySelectorAll("#table th[data-sort]").forEach(function (th) {
      th.tabIndex = 0;
      function toggle() {
        var key = th.dataset.sort;
        if (key === sortKey) { sortDesc = !sortDesc; }
        else {
          sortKey = key;
          // A number is nearly always wanted largest first; a name is not.
          sortDesc = th.classList.contains("num");
        }
        render();
      }
      th.addEventListener("click", toggle);
      th.addEventListener("keydown", function (event) {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          toggle();
        }
      });
    });

    // A new kind means a new set of metric names to choose from.
    kindBox.addEventListener("change", function () {
      setMetrics(metricBox.value);
      render();
      drawHistory();
    });
    metricBox.addEventListener("change", function () {
      render();
      drawHistory();
    });

    window.Sidebar.onChange(load);
    // A restored sidebar has already asked for this; see Sidebar.restored.
    if (!window.Sidebar.restored) { load(window.Sidebar.query()); }
  });
})();
