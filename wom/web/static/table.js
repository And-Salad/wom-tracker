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

  var whoBox = document.getElementById("who-filter");
  var kindBox = document.getElementById("kind");
  var metricBox = document.getElementById("metric");

  function matching() {
    var who = whoBox.value;
    var kind = kindBox.value;
    var metric = metricBox.value;
    var movedOnly = document.getElementById("moved").checked;
    return rows.filter(function (row) {
      if (row.kind !== kind) { return false; }
      if (who && row.username !== who) { return false; }
      if (metric && row.metric !== metric) { return false; }
      if (movedOnly && !row.gained) { return false; }
      return true;
    });
  }

  function fill(select, options, keep) {
    select.textContent = "";
    var wanted = keep;
    var has = options.some(function (o) { return o.value === wanted; });
    options.forEach(function (option) {
      var node = document.createElement("option");
      node.value = option.value;
      node.textContent = option.label;
      select.appendChild(node);
    });
    select.value = has ? wanted : options[0].value;
  }

  /* The players actually in the data, not the roster: this list and the
     sidebar can then never disagree about who is on the page. */
  function playerOptions() {
    var seen = {};
    var out = [{value: "", label: "All players"}];
    rows.forEach(function (row) {
      if (!seen[row.username]) {
        seen[row.username] = true;
        out.push({value: row.username, label: row.player});
      }
    });
    return out;
  }

  /* Metrics belong to a kind - there is no Zulrah among the skills - so the
     list is rebuilt whenever the kind changes rather than offering names
     that would match nothing. */
  function metricOptions(kind) {
    var seen = {};
    var out = [];
    rows.forEach(function (row) {
      if (row.kind === kind && !seen[row.metric]) {
        seen[row.metric] = true;
        out.push({value: row.metric, label: row.label});
      }
    });
    out.sort(function (a, b) { return a.label.localeCompare(b.label); });
    return [{value: "", label: "All " + kindName(kind)}].concat(out);
  }

  function kindName(kind) {
    var option = kindBox.querySelector('option[value="' + kind + '"]');
    return option ? option.textContent.toLowerCase() : "metrics";
  }

  // Skills, and within them the one line that sums the rest: the page opens
  // on six rows saying where each account stands, not 666 saying everything.
  var firstMetric = "overall";

  function refreshChoices() {
    fill(whoBox, playerOptions(), whoBox.value);
    fill(metricBox, metricOptions(kindBox.value), metricBox.value || firstMetric);
    firstMetric = null;       // only the first load gets the opening default
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
      dot.style.background = row.color;
      dot.style.display = "inline-block";
      dot.style.marginRight = "7px";
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

  function query() {
    var params = new URLSearchParams();
    params.set("period", document.getElementById("period").value);
    // Says the ticks are a real choice, so unticking everyone means nobody
    // rather than a bare link's "show me all of them".
    params.set("picked", "1");
    document.querySelectorAll(".side input[name=player]:checked")
      .forEach(function (box) { params.append("player", box.value); });
    return params.toString();
  }

  var queued = null;

  function load() {
    clearTimeout(queued);
    queued = setTimeout(function () {
      var q = query();
      history.replaceState(null, "", "/export?" + q);
      exportTargets();
      var mine = ++seq;
      state.style.display = "";
      state.textContent = "Loading…";
      fetch("/api/table?" + q).then(function (r) {
        if (!r.ok) { throw new Error(r.status === 429 ? "too many requests"
                                                      : "HTTP " + r.status); }
        return r.json();
      }).then(function (payload) {
        if (mine !== seq) { return; }
        rows = payload.rows || [];
        if (payload.empty) {
          body.textContent = "";
          count.textContent = "";
          state.textContent = payload.empty;
          return;
        }
        refreshChoices();
        render();
      }).catch(function (err) {
        if (mine !== seq) { return; }
        body.textContent = "";
        count.textContent = "";
        state.textContent = "Could not load the table: " + err.message;
      });
    }, 90);
  }

  // -- the export dialog ---------------------------------------------------

  var dialog = document.getElementById("export-dialog");
  var form = document.getElementById("export");

  /* The dialog has no player picker of its own: the sidebar already decides
     who the page is about, and two lists of the same six names disagreeing
     with each other is how the wrong file gets downloaded. */
  function exportTargets() {
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
    // Readings are stored in UTC; tell the server which day the viewer means,
    // or "to 30 August" would stop at 20:00 for anyone west of Greenwich.
    document.getElementById("tzoffset").value =
      String(-new Date().getTimezoneOffset());

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
      fill(metricBox, metricOptions(kindBox.value), metricBox.value);
      render();
    });
    [whoBox, metricBox, document.getElementById("moved")].forEach(function (node) {
      node.addEventListener("change", render);
    });

    document.getElementById("period").addEventListener("change", load);
    document.querySelectorAll(".side input[name=player]").forEach(function (box) {
      box.addEventListener("change", load);
    });
    load();
  });
})();
