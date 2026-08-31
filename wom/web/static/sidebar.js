/* The sidebar, which is the same control on every tab that has one.
 *
 * It owns three things - who is included, over what window, and keeping both
 * of those in the address bar - and tells the page when any of it changes.
 * A page registers what to do about that with Sidebar.onChange(); it never
 * reads the controls itself.
 *
 * The period select and the dates are one control, not two. Picking a preset
 * fills the dates from whatever the server resolved it to; typing in a date
 * moves the select to "Custom", which is what the server then honours. So
 * there is never a moment where one of them is quietly being ignored.
 */
(function () {
  "use strict";

  var CUSTOM = "Custom";

  var form = document.getElementById("filters");
  if (!form) { return; }                     // a bare page: nothing to drive

  var periodBox = document.getElementById("period");
  var fromBox = document.getElementById("from");
  var toBox = document.getElementById("to");
  var unlock = document.getElementById("unlock");
  var offsetBox = document.getElementById("tzoffset");
  var allNone = document.getElementById("all-none");
  var boxes = [].slice.call(form.querySelectorAll("input[name=player]"));

  var listeners = [];
  var queued = null;
  // Where the x goes back to. A custom range names no preset, so the last one
  // actually chosen is remembered rather than guessed at.
  var lastPreset = periodBox && periodBox.value !== CUSTOM
    ? periodBox.value : "Week";

  // Readings are stored in UTC; say which day the viewer means, or a "to" of
  // 30 August would stop at 20:00 for anyone west of Greenwich.
  if (offsetBox) { offsetBox.value = String(-new Date().getTimezoneOffset()); }

  function custom() {
    return !!periodBox && periodBox.value === CUSTOM;
  }

  function query() {
    var params = new URLSearchParams();
    // Says the ticks are a real choice, so unticking everyone means nobody
    // rather than a bare link's "show me all of them".
    params.set("picked", "1");
    boxes.forEach(function (box) {
      if (box.checked) { params.append("player", box.value); }
    });
    if (periodBox) {
      params.set("period", periodBox.value);
      params.set("tzoffset", offsetBox ? offsetBox.value : "0");
      if (custom()) {
        if (fromBox.value) { params.set("from", fromBox.value); }
        if (toBox.value) { params.set("to", toBox.value); }
      }
    }
    return params.toString();
  }

  /* The dates show whatever window is actually in force, so a preset has to
     be told what it resolved to. Pages hand back the server's answer. */
  function showWindow(span) {
    if (!span || custom() || !fromBox) { return; }
    if (span.from) { fromBox.value = span.from; }
    if (span.to) { toBox.value = span.to; }
  }

  function announce() {
    // Changing several ticks in a row should cost one round of requests, not
    // one per tick, so let the clicks settle first.
    clearTimeout(queued);
    queued = setTimeout(function () {
      var q = query();
      // Keep the address bar in step, so a view can be linked or reloaded.
      history.replaceState(null, "", window.location.pathname + "?" + q);
      followNav(q);
      listeners.forEach(function (fn) { fn(q); });
    }, 90);
  }

  /* Every tab reads the same sidebar, so moving between them has to carry it.
     Without this the control forgets itself five times over. */
  function followNav(q) {
    document.querySelectorAll("nav a").forEach(function (link) {
      var path = link.getAttribute("href").split("?")[0];
      if (path === "/admin") { return; }     // admin has no sidebar to carry
      link.setAttribute("href", path + "?" + q);
    });
  }

  function setPeriod(value) {
    periodBox.value = value;
    if (value !== CUSTOM) { lastPreset = value; }
    unlock.hidden = value !== CUSTOM;
    document.getElementById("dates").classList.toggle("locked", value === CUSTOM);
  }

  if (periodBox) {
    periodBox.addEventListener("change", function () {
      setPeriod(periodBox.value);
      announce();
    });
    // Typing in either date is what makes the window custom; nothing else does.
    [fromBox, toBox].forEach(function (box) {
      box.addEventListener("change", function () {
        setPeriod(CUSTOM);
        announce();
      });
    });
    unlock.addEventListener("click", function () {
      setPeriod(lastPreset);
      announce();
    });
    setPeriod(periodBox.value);
  }

  boxes.forEach(function (box) {
    box.addEventListener("change", function () {
      syncAllNone();
      announce();
    });
  });

  /* One button, two jobs, and it says which one it will do. Six accounts is
     enough that "just this one" means five clicks of unticking otherwise. */
  function syncAllNone() {
    if (!allNone) { return; }
    var on = boxes.filter(function (box) { return box.checked; }).length;
    allNone.textContent = on === boxes.length ? "None" : "All";
  }

  if (allNone) {
    allNone.addEventListener("click", function () {
      var wanted = allNone.textContent === "All";
      boxes.forEach(function (box) { box.checked = wanted; });
      syncAllNone();
      announce();
    });
    syncAllNone();
  }

  followNav(query());

  window.Sidebar = {
    query: query,
    showWindow: showWindow,
    custom: custom,
    onChange: function (fn) { listeners.push(fn); }
  };
})();
