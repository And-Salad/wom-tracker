/* The gallery: kind toggles, and a viewer for one picture at a time.
 *
 * The server renders every panel and every thumbnail, so a reader with no
 * JavaScript sees all of them and can still open any picture by its own URL;
 * this only hides a panel and offers a closer look.
 */
(function () {
  "use strict";

  var types = document.getElementById("types");
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

  if (types) {
    types.addEventListener("change", function (event) {
      if (event.target && event.target.type === "checkbox") { apply(); }
    });
  }

  var viewer = document.getElementById("viewer");
  var full = document.getElementById("viewer-image");
  var caption = document.getElementById("viewer-caption");
  var close = document.getElementById("viewer-close");
  // showModal is what gives us Escape, the backdrop and focus handling. With
  // no dialog support the thumbnails simply stay thumbnails, which is a worse
  // page rather than a broken one.
  if (!viewer || !full || !viewer.showModal) { return; }

  document.addEventListener("click", function (event) {
    var button = event.target.closest && event.target.closest(".shot");
    if (!button) { return; }
    full.src = button.getAttribute("data-full");
    full.alt = button.getAttribute("data-caption") || "";
    caption.textContent = button.getAttribute("data-caption") || "";
    viewer.showModal();
  });

  if (close) {
    close.addEventListener("click", function () { viewer.close(); });
  }

  /* A click on the backdrop lands on the dialog itself, never on what is
     inside it, which is how one listener tells the two apart. */
  viewer.addEventListener("click", function (event) {
    if (event.target === viewer) { viewer.close(); }
  });

  /* Escape is meant to close a modal dialog on its own, and in at least one
     browser it does not - the key arrives, the dialog stays. Closing it here
     costs a listener and means the way out never depends on that. */
  document.addEventListener("keydown", function (event) {
    if (viewer.open && (event.key === "Escape" || event.key === "Esc")) {
      event.preventDefault();
      viewer.close();
    }
  });

  /* Let go of the picture on the way out: ten screenshots is already a lot to
     hold, and the browser has no reason to keep an eleventh copy decoded. */
  viewer.addEventListener("close", function () {
    full.removeAttribute("src");
  });
})();
