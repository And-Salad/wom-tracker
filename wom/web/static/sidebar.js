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
 *
 * Whatever it is set to is also kept in the browser, so opening the site
 * tomorrow starts where today left off rather than back at everyone over the
 * last week. A URL always wins over that - see restore().
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
  var offset = String(-new Date().getTimezoneOffset());
  if (offsetBox) { offsetBox.value = offset; }
  /* Also left where the server can find it. A page is rendered before this
     script runs, so the first paint has no query string to read - and the
     dates in the sidebar would be worked out in UTC, showing an Eastern
     reader a "To" of tomorrow every evening. Nothing but a number of
     minutes, and it only has to survive to the next request. */
  try {
    document.cookie = "wom_tz=" + encodeURIComponent(offset) +
      ";path=/;max-age=31536000;samesite=lax" +
      (window.location.protocol === "https:" ? ";secure" : "");
  } catch (e) { /* cookies refused: the query string still carries it */ }

  function custom() {
    return !!periodBox && periodBox.value === CUSTOM;
  }

  // One saved sidebar for the whole site, because it is one control: the
  // tabs already carry it between each other, and a stored copy per page
  // would mean Overview and Milestones disagreeing about who is included.
  var SAVED = "sidebar";
  var remember = (window.WOM && window.WOM.Remember) ||
    {read: function (_n, fallback) { return fallback; }, write: function () {}};

  function saved() {
    return new URLSearchParams(remember.read(SAVED, "") || "");
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
    } else {
      /* Recaps has ticks but no period control, and its links still have to
         carry the window: without this, going Recaps to Overview handed the
         server a URL naming no period and dropped the reader back to the
         default. The last saved one is the one the pages that own the
         control left behind. */
      var kept = saved();
      ["period", "from", "to", "tzoffset"].forEach(function (name) {
        if (kept.get(name)) { params.set(name, kept.get(name)); }
      });
    }
    return params.toString();
  }

  function save() {
    remember.write(SAVED, query());
  }

  /* What the sidebar comes back to on a bare URL.
   *
   * Only a bare one. A link with a query string says who and when in as many
   * words - it is how the tabs carry the sidebar and how a view gets shared -
   * and quietly overruling that with something out of this browser would make
   * a pasted link mean different things to the two people reading it.
   *
   * Returns whether anything actually moved, because the page behind it was
   * rendered from the bare URL and has to be told. */
  function restore() {
    if (window.location.search) { return false; }
    var kept = saved();
    if (!kept.get("picked")) { return false; }      // nothing saved yet

    var before = query();
    var wanted = kept.getAll("player");
    boxes.forEach(function (box) {
      box.checked = wanted.indexOf(box.value) !== -1;
    });
    syncAllNone();

    var period = kept.get("period");
    // An account that has since been untracked, or a period that is no
    // longer offered, is simply not restored; the rest of the sidebar still
    // is. Stored state is a convenience, and it outlives the settings.
    if (periodBox && period && offered(period)) {
      if (period === CUSTOM) {
        if (kept.get("from")) { fromBox.value = kept.get("from"); }
        if (kept.get("to")) { toBox.value = kept.get("to"); }
      }
      setPeriod(period);
    }
    return query() !== before;
  }

  function offered(period) {
    return [].some.call(periodBox.options, function (option) {
      return option.value === period;
    });
  }

  /* The dates show whatever window is actually in force, so a preset has to
     be told what it resolved to. Pages hand back the server's answer. */
  function showWindow(span) {
    if (!span || custom() || !fromBox) { return; }
    if (span.from) { fromBox.value = span.from; }
    if (span.to) { toBox.value = span.to; }
  }

  /* A page with nothing to refetch reloads instead. Round-ups is a document,
     not a dashboard - its tree is server-rendered text - and rebuilding it in
     JavaScript would duplicate the template and lose which folders are open.
     Reloading is the honest way for its ticks to mean something; without it
     they moved the address bar and changed nothing on screen. */
  var reloads = form.dataset.reload === "1";

  function announce() {
    // Changing several ticks in a row should cost one round of requests, not
    // one per tick, so let the clicks settle first.
    clearTimeout(queued);
    queued = setTimeout(function () {
      var q = query();
      save();
      if (reloads) {
        window.location.search = q;
        return;
      }
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

  /* The page behind a restored sidebar was rendered from the bare URL, so it
     is showing something the controls no longer say. A page that refetches is
     told the usual way. One that does not - Recaps says so on the form, and
     Gallery's panels are server-rendered with no listener at all - has to be
     asked for again, or the ticks and what is on screen disagree.

     Left until the page has registered its listener, which is what says
     which of the two it is. Asked any earlier, every page looks like one
     that does not refetch and every restore costs a second render. */
  function adopt() {
    if (reloads || !listeners.length) {
      window.location.search = query();
      return;
    }
    announce();
  }

  // Before followNav, so the tabs carry the restored sidebar rather than the
  // default one even if the page itself is about to be replaced.
  var restored = restore();
  if (restored) { whenReady(adopt); }
  followNav(query());

  /* After every DOMContentLoaded handler, not as one of them. A page
     registers its listener inside its own, and this file's would run first -
     a timer set from there is the earliest moment all of them have. */
  function whenReady(fn) {
    if (document.readyState !== "loading") {
      setTimeout(fn, 0);
      return;
    }
    document.addEventListener("DOMContentLoaded", function () {
      setTimeout(fn, 0);
    });
  }

  window.Sidebar = {
    query: query,
    showWindow: showWindow,
    custom: custom,
    /* True when the sidebar came back from the browser rather than from the
       URL, which means a first round of requests is already on its way. A
       page that fetches its own opening copy - the charts do; the feeds are
       rendered by the server - skips it and lets that one arrive, rather
       than asking for the same thing twice on every morning's first visit. */
    restored: restored,
    onChange: function (fn) { listeners.push(fn); }
  };
})();
