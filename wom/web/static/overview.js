/* The Overview page: five cards, all fed from the sidebar.
 *
 * The drawing lives in chartkit.js and the controls in sidebar.js; this is
 * only the wiring between them.
 */
(function () {
  "use strict";

  var Chart = window.WOM.Chart;
  var charts = [];

  function loadAll(query) {
    charts.forEach(function (chart) { chart.load(query); });
  }

  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll(".card.chart").forEach(function (node) {
      charts.push(new Chart(node));
    });
    // A card's own dropdown affects only that card, so it does not go through
    // the sidebar.
    document.querySelectorAll("select.choice").forEach(function (select) {
      select.addEventListener("change", function () {
        var card = select.closest(".card.chart");
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
        charts.filter(function (c) { return c.node === card; })
          .forEach(function (c) { c.setMode(button.dataset.mode); });
      });
    });
    window.Sidebar.onChange(loadAll);
    loadAll(window.Sidebar.query());
  });
})();
