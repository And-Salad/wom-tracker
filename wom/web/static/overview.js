/* The Overview page: five cards, all fed from the period and the player ticks.
 *
 * The drawing lives in chartkit.js; this is only the wiring.
 */
(function () {
  "use strict";

  var Chart = window.WOM.Chart;

  var charts = [];

  function query() {
    var params = new URLSearchParams();
    params.set("period", document.getElementById("period").value);
    // Says the ticks below are a real choice, so unticking everyone means
    // nobody rather than a bare link's "show me all of them".
    params.set("picked", "1");
    document.querySelectorAll("input[name=player]:checked").forEach(function (box) {
      params.append("player", box.value);
    });
    return params.toString();
  }

  var queued = null;

  function loadAll() {
    // Ticking a run of boxes should cost one round of requests, not one per
    // box, so let the clicks settle first.
    clearTimeout(queued);
    queued = setTimeout(function () {
      var q = query();
      // Keep the address bar in step, so a view can be linked or reloaded.
      history.replaceState(null, "", "/?" + q);
      charts.forEach(function (chart) { chart.load(q); });
    }, 90);
  }

  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll(".card.chart").forEach(function (node) {
      charts.push(new Chart(node));
    });
    document.getElementById("period").addEventListener("change", loadAll);
    document.querySelectorAll("input[name=player]").forEach(function (box) {
      box.addEventListener("change", loadAll);
    });
    document.querySelectorAll("select.choice").forEach(function (select) {
      select.addEventListener("change", function () {
        var card = select.closest(".card.chart");
        charts.filter(function (c) { return c.node === card; })
          .forEach(function (c) { c.load(query()); });
      });
    });
    loadAll();
  });
})();
