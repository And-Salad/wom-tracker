/* The expanding player rows.
 *
 * The table row is the control - there is one list of players on this page,
 * not a table and an accordion repeating each other. A player's detail is
 * three trees, and every player's worth of that is a few hundred kilobytes
 * nobody has asked to see, so it is fetched when a row is first opened and
 * again only if the period changes underneath it.
 */
(function () {
  "use strict";

  var full = new Intl.NumberFormat();

  function periodValue() {
    var picker = document.getElementById("period");
    return picker ? picker.value : "Week";
  }

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) { node.className = className; }
    if (text !== undefined) { node.textContent = text; }
    return node;
  }

  function iconFor(kind, metric) {
    var img = el("img", "row-icon");
    img.src = "/icon/" + (kind === "activity" ? "activity" : kind) + "/" + metric + ".png";
    img.alt = "";
    img.loading = "lazy";
    // Not every metric has a sprite; the label alone carries it then.
    img.addEventListener("error", function () { img.remove(); });
    return img;
  }

  function measure(row, kind) {
    if (kind === "skill") {
      return [row.level === null ? "-" : "level " + full.format(row.level),
              row.value === null ? "" : full.format(row.value) + " XP"];
    }
    if (kind === "boss") {
      return [row.value === null ? "unranked" : full.format(row.value) + " kills", ""];
    }
    return [row.value === null ? "-" : full.format(row.value), ""];
  }

  function groupNode(group, openByDefault) {
    var box = el("details", "folder");
    // Skills first and already open: it is the group with something in every
    // row, where bosses are mostly zeroes on any short period.
    if (openByDefault) { box.open = true; }
    var head = el("summary", "node");
    head.appendChild(el("span", null, group.title));
    head.appendChild(el("span", "count",
      group.moved ? group.moved + " moved of " + group.rows.length
                  : group.rows.length + " tracked"));
    box.appendChild(head);

    if (!group.rows.length) {
      box.appendChild(el("p", "hint", "Nothing stored for this yet."));
      return box;
    }

    var table = el("table", "detail-table");
    group.rows.forEach(function (row) {
      var tr = el("tr", row.gained ? "moved" : null);
      var name = el("td", "name");
      name.appendChild(iconFor(group.kind, row.metric));
      name.appendChild(el("span", null, row.label));
      tr.appendChild(name);
      var parts = measure(row, group.kind);
      // The last two are dropped on a phone, where the gain is what matters
      // and five columns would push it off the edge.
      tr.appendChild(el("td", "num measure", parts[0]));
      tr.appendChild(el("td", "num dim wide-only", parts[1]));
      tr.appendChild(el("td", "num gain",
        row.gained ? "+" + full.format(Math.round(row.gained)) : ""));
      tr.appendChild(el("td", "num dim wide-only",
        row.rank === null || row.rank === undefined ? "" : "#" + full.format(row.rank)));
      table.appendChild(tr);
    });
    var scroll = el("div", "scroll");
    scroll.appendChild(table);
    box.appendChild(scroll);
    return box;
  }

  function render(host, data) {
    host.textContent = "";
    var moved = data.groups.reduce(function (n, g) { return n + g.moved; }, 0);
    host.appendChild(el("p", "hint",
      data.period + " · " + moved + " metric" + (moved === 1 ? "" : "s") + " moved"));
    // Wise Old Man only has the readings it has. Say when the figures cover
    // less than the period asks for, or a week nobody watched reads as a
    // quiet week rather than an unmeasured one.
    if (data.coverage && data.coverage.short) {
      host.appendChild(el("p", "warn-note", data.coverage.note));
    }
    data.groups.forEach(function (group, index) {
      host.appendChild(groupNode(group, index === 0));
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    var rows = [].slice.call(document.querySelectorAll("tr.player-row"));
    if (!rows.length) { return; }

    rows.forEach(function (row) {
      var detailRow = row.nextElementSibling;
      var host = detailRow.querySelector(".detail-body");
      var shown = null;              // the period the body currently reflects

      function open() { return !detailRow.hidden; }

      function load() {
        var wanted = periodValue();
        if (shown === wanted) { return; }
        host.textContent = "";
        host.appendChild(el("p", "hint", "Loading..."));
        var mine = wanted;
        fetch("/api/player/" + encodeURIComponent(row.dataset.username) +
              "?period=" + encodeURIComponent(wanted))
          .then(function (r) { return r.json(); })
          .then(function (data) {
            if (mine !== periodValue()) { return; }   // the period moved on
            shown = mine;
            render(host, data);
          })
          .catch(function (err) {
            host.textContent = "";
            host.appendChild(el("p", "hint", "Could not load this player: " + err));
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
      row.__reload = function () { shown = null; if (open()) { load(); } };
    });

    var picker = document.getElementById("period");
    if (picker) {
      picker.addEventListener("change", function () {
        // Keep the address bar in step so the view can be linked.
        var params = new URLSearchParams(location.search);
        params.set("period", picker.value);
        history.replaceState(null, "", "/players?" + params.toString());
        rows.forEach(function (row) { row.__reload(); });
      });
    }
  });
})();
