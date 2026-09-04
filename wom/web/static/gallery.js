/* The gallery's kind toggles.
 *
 * The server renders every panel, so a reader with no JavaScript sees all of
 * them; this only hides one when its box is unticked.
 */
(function () {
  "use strict";

  var types = document.getElementById("types");
  if (!types) { return; }

  var panels = document.querySelectorAll(".panel[data-category]");

  function apply() {
    var wanted = Object.create(null);
    Array.prototype.forEach.call(
      types.querySelectorAll("input[type=checkbox]"),
      function (box) { wanted[box.value] = box.checked; });
    Array.prototype.forEach.call(panels, function (panel) {
      var kind = panel.getAttribute("data-category");
      // A kind with no box stays visible: the server decides what exists.
      panel.hidden = kind in wanted && !wanted[kind];
    });
  }

  types.addEventListener("change", function (event) {
    if (event.target && event.target.type === "checkbox") { apply(); }
  });
})();
