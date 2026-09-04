/* The Overview page: five cards, all fed from the sidebar.
 *
 * The drawing lives in chartkit.js and the controls in sidebar.js; this is
 * only the wiring between them.
 */
(function () {
  "use strict";

  var Chart = window.WOM.Chart;
  var charts = [];

  /* Each card's own dropdown and mode buttons, kept between visits. Both are
     per card, so they are stored under the card's key: someone who watches
     Slayer on one card and reads another as a rate is asking the same two
     questions every morning.

     One entry per card rather than one blob for the page, so a card that is
     added, removed or renamed only ever loses its own setting. */
  var remember = (window.WOM && window.WOM.Remember) ||
    {read: function (_n, fallback) { return fallback; }, write: function () {}};

  function held(card, what) {
    return "overview." + (card.dataset.key || "?") + "." + what;
  }

  function loadAll(query) {
    charts.forEach(function (chart) { chart.load(query); });
  }

  /* A stored choice is only honoured if the card still offers it: the options
     come from the catalogue on the server, and a metric can stop being one. */
  function restoreChoice(select, card) {
    var kept = remember.read(held(card, "choice"), null);
    var found = Array.prototype.some.call(select.options, function (option) {
      return option.value === kept;
    });
    if (found) { select.value = kept; }
  }

  /* Restored by pressing the button, not by telling the chart: the Chart
     reads its mode off aria-pressed when it is constructed, so moving the
     button before then is the whole of it. A stored mode a card no longer
     offers is ignored, and its first button stays pressed. */
  function restoreMode(group, card) {
    var kept = remember.read(held(card, "mode"), null);
    var buttons = [].slice.call(group.querySelectorAll("button.mode"));
    var wanted = buttons.filter(function (button) {
      return button.dataset.mode === kept;
    })[0];
    if (!wanted) { return; }
    buttons.forEach(function (button) {
      button.setAttribute("aria-pressed", String(button === wanted));
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    // Both controls are put back before the first fetch, so the card is drawn
    // once, showing what was asked for - not drawn on the default and then
    // immediately fetched again.
    document.querySelectorAll("select.choice").forEach(function (select) {
      restoreChoice(select, select.closest(".card.chart"));
    });
    document.querySelectorAll(".card.chart .modes").forEach(function (group) {
      restoreMode(group, group.closest(".card.chart"));
    });

    document.querySelectorAll(".card.chart").forEach(function (node) {
      charts.push(new Chart(node));
    });
    // A card's own dropdown affects only that card, so it does not go through
    // the sidebar.
    document.querySelectorAll("select.choice").forEach(function (select) {
      select.addEventListener("change", function () {
        var card = select.closest(".card.chart");
        remember.write(held(card, "choice"), select.value);
        charts.filter(function (c) { return c.node === card; })
          .forEach(function (c) { c.load(window.Sidebar.query()); });
      });
    });
    // A card's mode buttons change how its figures are read, not which
    // figures they are, so they redraw the card rather than refetching it.
    document.querySelectorAll(".card.chart .modes").forEach(function (group) {
      group.addEventListener("click", function (event) {
        var button = event.target.closest("button.mode");
        if (!button) { return; }
        group.querySelectorAll("button.mode").forEach(function (other) {
          other.setAttribute("aria-pressed", String(other === button));
        });
        var card = group.closest(".card.chart");
        remember.write(held(card, "mode"), button.dataset.mode);
        charts.filter(function (c) { return c.node === card; })
          .forEach(function (c) { c.setMode(button.dataset.mode); });
      });
    });
    window.Sidebar.onChange(loadAll);
    // A restored sidebar has already asked for these; see Sidebar.restored.
    if (!window.Sidebar.restored) { loadAll(window.Sidebar.query()); }
  });
})();
