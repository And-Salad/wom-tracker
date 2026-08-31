// Say roughly how much is being asked for, so a year of everything is not a
  // surprise when it lands.
  (function () {
    // Readings are stored in UTC; tell the server which day the viewer means,
    // or "to 30 August" would stop at 20:00 for anyone west of Greenwich.
    var tz = document.getElementById("tzoffset");
    if (tz) { tz.value = String(-new Date().getTimezoneOffset()); }

    var form = document.getElementById("export");
    var out = document.getElementById("estimate");
    function count() {
      var players = form.querySelectorAll("input[name=player]:checked").length;
      var kinds = form.querySelectorAll("input[name=kind]:checked").length;
      if (!players) { out.textContent = "Pick at least one player."; return; }
      if (!kinds) { out.textContent = "Pick at least one kind of metric."; return; }
      out.textContent = players + (players === 1 ? " player" : " players") +
        " across " + kinds + (kinds === 1 ? " kind" : " kinds") + " of metric.";
    }
    form.addEventListener("change", count);
    count();
  })();
