/* The leaderboards page: Maxing and Grinding, one on screen at a time.
 *
 * Both are rendered by the server, which puts the one not chosen away; this
 * switches between them, remembers which was being read, and wires up what
 * each board can do:
 *
 *   - expanding standings rows, fetched when a row is first opened
 *   - the day's trend, drawn when its board is first looked at
 *
 * A row is the control, the same as on Players - there is one list of
 * accounts per board here, not a table and an accordion repeating each other.
 *
 * The ticks do not reload this page (there is a calendar to keep), so the
 * charts listen for a change of window themselves.
 */
(function () {
  "use strict";

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
  function render(host, board, data) {
    host.textContent = "";
    if (data.note) {
      host.appendChild(el("p", "hint", data.note));
      return;
    }
    /* Named for what this board counts. One script serves both, and on
       Grinding "toward 99" describes a rule it does not have. */
    var head = (board === "grinding" ? "Gained today: " : "Toward 99 today: ") +
      full.format(Math.round(data.total)) + " XP";
    if (data.nines) {
      head += " · " + data.nines + " ninety-nine" + (data.nines === 1 ? "" : "s");
    }
    if (data.beyond) {
      head += " · " + full.format(Math.round(data.beyond)) +
        " XP past 99, which the day is not judged on";
    }
    host.appendChild(el("p", "hint", head));

    var table = el("table", "detail-table");
    data.rows.forEach(function (row) { table.appendChild(skillRow(row)); });
    var scroll = el("div", "scroll");
    scroll.appendChild(table);
    host.appendChild(scroll);
  }

  function wire(row, board) {
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
          render(host, board, data);
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

  /* Which board was being read, kept between visits. Stored as the one chosen
     rather than the ones not: there are two and both are always offered, so a
     name that stops existing simply fails to match and the server's choice
     stands. */
  var CHOSEN = "board.shown";

  document.addEventListener("DOMContentLoaded", function () {
    var sections = document.querySelectorAll(".board-page[data-board]");
    if (!sections.length) { return; }
    var picker = document.getElementById("boards");
    var remember = (window.WOM && window.WOM.Remember) ||
      {read: function (_n, fallback) { return fallback; }, write: function () {}};

    /* One entry per board: its section, its chart, and whether what the chart
       holds is still the window the sidebar is on. A hidden chart is left
       alone - drawn into a hidden card it comes out with no width - so it is
       marked stale instead and redrawn when its board is shown. */
    var boards = [];
    Array.prototype.forEach.call(sections, function (section) {
      var name = section.getAttribute("data-board");
      section.querySelectorAll("tr.today-row").forEach(function (row) {
        wire(row, name);
      });
      boards.push({name: name, section: section, chart: null, stale: true});
    });

    /* Nothing is drawn until the page has settled on which board is shown
       and on where its opening copy is coming from - a restored sidebar has
       already asked for one, and drawing here too would fetch the same day
       twice on the first visit of every morning. */
    var ready = false;

    function draw(entry) {
      if (!ready || entry.section.hidden || !entry.stale) { return; }
      if (!window.WOM || !window.Sidebar) { return; }
      if (!entry.chart) {
        var card = entry.section.querySelector(".board-trend");
        if (!card) { return; }
        entry.chart = new window.WOM.Chart(card);
        /* Always the day in progress, never the sidebar's period - so this
           asks its own endpoint rather than pretending to be a catalogue
           entry. */
        entry.chart.endpoint = function () {
          return "/api/" + entry.name + "/trend?" + window.Sidebar.query();
        };
      }
      entry.stale = false;
      entry.chart.load(window.Sidebar.query());
    }

    function show(board) {
      if (!boards.some(function (entry) { return entry.name === board; })) {
        return false;
      }
      boards.forEach(function (entry) {
        entry.section.hidden = entry.name !== board;
        var radio = picker &&
          picker.querySelector('input[value="' + entry.name + '"]');
        if (radio) { radio.checked = entry.name === board; }
        draw(entry);
      });
      return true;
    }

    if (picker) {
      picker.addEventListener("change", function (event) {
        var chosen = event.target;
        if (!chosen || chosen.type !== "radio") { return; }
        remember.write(CHOSEN, chosen.value);
        show(chosen.value);
      });
    }

    /* A URL asking for a board wins over what was last read: it is a link
       somebody followed to that board on purpose. Only a bare /leaderboards
       falls back to what they were looking at last. */
    var asked = new URLSearchParams(window.location.search).get("board");
    var kept = asked || remember.read(CHOSEN, null);
    // A board that is no longer offered leaves the server's default in place.
    if (kept) { show(kept); }

    if (!window.Sidebar) { return; }
    /* The ticks are the one thing on this page that moves anything. The
       calendar and the standings judge every account whatever is ticked, so
       there is nothing to reload - the charts redraw and they stay put. */
    window.Sidebar.onChange(function () {
      boards.forEach(function (entry) {
        entry.stale = true;
        draw(entry);
      });
    });

    ready = true;
    // A restored sidebar has already asked for this; see Sidebar.restored.
    if (!window.Sidebar.restored) {
      boards.forEach(draw);
    }
  });
})();
