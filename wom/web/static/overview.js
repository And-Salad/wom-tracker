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
    window.Sidebar.onChange(loadAll);
    loadAll(window.Sidebar.query());
  });
})();
