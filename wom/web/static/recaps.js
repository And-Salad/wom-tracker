/* The recaps page: one leaderboard's round-ups at a time.
 *
 * Both boards are rendered by the server, so a reader with no JavaScript sees
 * every round-up rather than none; this only hides the one not chosen.
 *
 * The whole page is one fragment (fragments.js): a recap landing, or the
 * ticks changing who is included, brings the server's new copy across in
 * place. So nothing here holds on to an element - the toggle is found again
 * every time, and the board being read is put back after each refresh.
 */
(function () {
  "use strict";

  /* Which leaderboard was being read, kept between visits. Stored as the one
     chosen rather than the ones not: there are two of them and both are
     always offered, so a name that stops existing simply fails to match and
     the default stands. */
  var CHOSEN = "recaps.board";
  var remember = (window.WOM && window.WOM.Remember) ||
    {read: function (_n, fallback) { return fallback; }, write: function () {}};
  var current = remember.read(CHOSEN, null);

  function show(board) {
    var picker = document.getElementById("boards");
    if (!picker || !board) { return false; }
    var found = false;
    Array.prototype.forEach.call(picker.querySelectorAll("input[type=radio]"),
      function (radio) {
        if (radio.value !== board) { return; }
        radio.checked = true;
        found = true;
      });
    if (!found) { return false; }
    Array.prototype.forEach.call(
      document.querySelectorAll(".board-feed[data-board]"), function (feed) {
        feed.hidden = feed.getAttribute("data-board") !== board;
      });
    return true;
  }

  document.addEventListener("change", function (event) {
    var chosen = event.target;
    if (!chosen || chosen.type !== "radio" || !chosen.closest("#boards")) {
      return;
    }
    current = chosen.value;
    remember.write(CHOSEN, current);
    show(current);
  });

  // The server's copy arrives with its own default board ticked.
  document.addEventListener("wom:refreshed", function () { show(current); });

  // A board that is no longer offered leaves the server's default in place.
  show(current);

  /* Who is included is a query the server answers, so a tick is the page
     fetched again - in place, rather than the reload it used to be. */
  if (window.Sidebar && window.WOM && window.WOM.Fragments) {
    window.Sidebar.onChange(function () { window.WOM.Fragments.refresh(); });
  }
})();
