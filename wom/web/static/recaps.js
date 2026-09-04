/* The recaps page: one leaderboard's round-ups at a time.
 *
 * Both boards are rendered by the server, so a reader with no JavaScript sees
 * every round-up rather than none; this only hides the one not chosen.
 */
(function () {
  "use strict";

  var picker = document.getElementById("boards");
  if (!picker) { return; }

  var feeds = document.querySelectorAll(".board-feed[data-board]");

  /* Which leaderboard was being read, kept between visits. Stored as the one
     chosen rather than the ones not: there are two of them and both are
     always offered, so a name that stops existing simply fails to match and
     the default stands. */
  var CHOSEN = "recaps.board";
  var remember = (window.WOM && window.WOM.Remember) ||
    {read: function (_n, fallback) { return fallback; }, write: function () {}};

  function show(board) {
    var found = false;
    Array.prototype.forEach.call(picker.querySelectorAll("input[type=radio]"),
      function (radio) {
        if (radio.value !== board) { return; }
        radio.checked = true;
        found = true;
      });
    if (!found) { return false; }
    Array.prototype.forEach.call(feeds, function (feed) {
      feed.hidden = feed.getAttribute("data-board") !== board;
    });
    return true;
  }

  picker.addEventListener("change", function (event) {
    var chosen = event.target;
    if (!chosen || chosen.type !== "radio") { return; }
    remember.write(CHOSEN, chosen.value);
    show(chosen.value);
  });

  // A board that is no longer offered leaves the server's default in place.
  var kept = remember.read(CHOSEN, null);
  if (kept) { show(kept); }
})();
