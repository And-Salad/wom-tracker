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

  picker.addEventListener("change", function (event) {
    var chosen = event.target;
    if (!chosen || chosen.type !== "radio") { return; }
    Array.prototype.forEach.call(feeds, function (feed) {
      feed.hidden = feed.getAttribute("data-board") !== chosen.value;
    });
  });
})();
