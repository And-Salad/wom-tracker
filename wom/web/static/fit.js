/* A `.scroll` box is a scroller only while it needs to be.
 *
 * `overflow-x: auto` already hides its own scrollbar when the contents fit,
 * but not for everyone: a viewer whose system draws classic scrollbars gets a
 * full-width track under a table with room to spare, and a table whose width
 * lands on a fraction of a pixel can be "overflowing" by half a pixel that
 * nobody can see. Both look like the page is hiding a column.
 *
 * So the measuring happens here, and the CSS keeps `auto` as its default:
 * without this file every box stays a scroller, which is the safe way round.
 */
(function () {
  "use strict";

  function boxes() {
    // The Data grid scrolls in both directions on purpose; leave it be.
    return [].slice.call(document.querySelectorAll(".scroll:not(.grid-scroll)"));
  }

  function fit() {
    boxes().forEach(function (box) {
      // Measure with the scroller off. A box that is only overflowing
      // because a scrollbar is taking up room would never settle otherwise.
      box.classList.add("fits");
      if (box.scrollWidth > box.clientWidth + 1) {
        box.classList.remove("fits");
      }
    });
  }

  window.addEventListener("resize", fit);
  document.addEventListener("DOMContentLoaded", function () {
    fit();
    // Several tables are rebuilt in place when the sidebar changes, and a
    // new set of rows can be wider than the old one.
    if (!window.MutationObserver) { return; }
    var watch = new MutationObserver(fit);
    boxes().forEach(function (box) {
      watch.observe(box, { childList: true, subtree: true, characterData: true });
    });
  });
  fit();
})();
