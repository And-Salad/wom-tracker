/* A leaderboard page - Maxing or Grinding: expanding standings rows, and
 * the day's trend. Which board is on screen comes off the chart card, so one
 * script serves both.
 *
 * The row is the control, the same as on Players - there is one list of
 * accounts here, not a table and an accordion repeating each other. A day's
 * breakdown is small, but it is only interesting for the account somebody
 * asks about, so it is fetched when a row is first opened.
 *
 * The ticks reload this page (there is a calendar to redraw), so nothing here
 * listens for a change of window.
 */
(function () {
  "use strict";

  var host = document.getElementById("board-trend");
  var board = (host && host.dataset.board) || "maxing";

  var full = new Intl.NumberFormat();

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) { node.className = className; }
    if (text !== undefined) { node.textContent = text; }
    return node;
  }

  function iconFor(metric) {
    var img = el("img", "row-icon");
    img.src = "/icon/skill/" + metric + ".png";
    img.alt = "";
    img.loading = "lazy";
    // Not every metric has a sprite; the label alone carries it then.
    img.addEventListener("error", function () { img.remove(); });
    return img;
  }

  function skillRow(row) {
    var tr = el("tr", "moved");
    var name = el("td", "name");
    name.appendChild(iconFor(row.metric));
    name.appendChild(el("span", null, row.label));
    // A 99 reached today is the thing the day is actually won on, so it is
    // marked rather than left to be inferred from the number beside it.
    if (row.reached_99) { name.appendChild(el("span", "badge", "99")); }
    tr.appendChild(name);

    tr.appendChild(el("td", "num gain",
      row.capped ? "+" + full.format(Math.round(row.capped)) : "-"));
    /* Experience past 99 counts for nothing in the ranking, which is exactly
       why it is worth a column: without it a day of heavy training in a maxed
       skill reads as a day of doing nothing at all. */
    tr.appendChild(el("td", "num dim wide-only",
      row.beyond ? "+" + full.format(Math.round(row.beyond)) + " past 99" : ""));
    return tr;
  }

  /* Skills and their experience, and nothing else. An account's written
     recaps are read on the Recaps page, where every window it has is in one
     tree; here they would push the day's figures - which are what the row
     was opened for - below a fold of prose. */
  function render(host, data) {
    host.textContent = "";
    if (data.note) {
      host.appendChild(el("p", "hint", data.note));
      return;
    }
    var head = "Toward 99 today: " + full.format(Math.round(data.total)) + " XP";
    if (data.nines) {
      head += " · " + data.nines + " ninety-nine" + (data.nines === 1 ? "" : "s");
    }
    if (data.beyond) {
      head += " · " + full.format(Math.round(data.beyond)) + " XP past 99, which the day is not judged on";
    }
    host.appendChild(el("p", "hint", head));

    var table = el("table", "detail-table");
    data.rows.forEach(function (row) { table.appendChild(skillRow(row)); });
    var scroll = el("div", "scroll");
    scroll.appendChild(table);
    host.appendChild(scroll);
  }

  function wire(row) {
    var detailRow = row.nextElementSibling;
    var host = detailRow.querySelector(".detail-body");
    var loaded = false;

    function open() { return !detailRow.hidden; }

    function load() {
      if (loaded) { return; }
      host.textContent = "";
      host.appendChild(el("p", "hint", "Loading..."));
      fetch("/api/" + board + "/player/" + encodeURIComponent(row.dataset.username))
        .then(function (r) { return r.json(); })
        .then(function (data) {
          loaded = true;
          render(host, data);
        })
        .catch(function (err) {
          host.textContent = "";
          host.appendChild(el("p", "hint", "Could not load this day: " + err));
        });
    }

    function toggle() {
      detailRow.hidden = open();
      row.classList.toggle("open", !detailRow.hidden);
      row.setAttribute("aria-expanded", String(!detailRow.hidden));
      if (!detailRow.hidden) { load(); }
    }

    row.addEventListener("click", toggle);
    // The row is a button, so it answers to the keyboard like one.
    row.addEventListener("keydown", function (event) {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        toggle();
      }
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll("tr.today-row").forEach(wire);

    var card = document.getElementById("board-trend");
    if (!card || !window.WOM) { return; }
    var chart = new window.WOM.Chart(card);
    /* Always the day in progress, never the sidebar's period - so this asks
       its own endpoint rather than pretending to be a catalogue entry. */
    chart.endpoint = function () { return "/api/" + board + "/trend?" + window.Sidebar.query(); };
    /* The only thing on this page the ticks move. The calendar and the
       standings above judge every account whatever is ticked, so the page
       has nothing to reload - this redraws and they stay as they are. */
    window.Sidebar.onChange(function (query) { chart.load(query); });
    chart.load(window.Sidebar.query());
  });
})();
