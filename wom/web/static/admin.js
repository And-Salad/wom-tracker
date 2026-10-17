// Confirming a destructive action, out here rather than as an inline
// onsubmit, so the page needs no inline script and the CSP can forbid it.
(function () {
  var prune = document.getElementById("prune");
  if (prune) {
    prune.addEventListener("submit", function (event) {
      if (!confirm("Delete every stored snapshot for players no longer " +
                   "on the list?")) {
        event.preventDefault();
      }
    });
  }
})();

// Poll while something is running, so the page shows progress without a reload.
  (function () {
    var box = document.getElementById("job");
    if (!box) { return; }
    var note = box.querySelector(".job-note");
    var out = box.querySelector(".job-log");
    function tick() {
      fetch("/admin/status").then(function (r) { return r.json(); }).then(function (job) {
        note.textContent = job.note;
        out.textContent = (job.lines || []).join("\n");
        box.classList.toggle("busy", !!job.running);
        box.classList.toggle("failed", !!job.failed);
        if (job.running) { setTimeout(tick, 1200); }
      }).catch(function () { /* the server will be back */ });
    }
    tick();
  })();

// A webhook URL is longer than the box it sits in, and selecting it by hand
// copies whatever happened to be visible - a half-copied URL then answers 404
// with nothing to say why. Clicking selects all of it, and the button copies
// it outright where the browser allows.
//
// Delegated from the document rather than bound to each field, because the
// table they sit in is replaced as session events land (fragments.js).
(function () {
  function select(event) {
    var field = event.target;
    if (field && field.classList && field.classList.contains("secret")) {
      field.select();
    }
  }
  document.addEventListener("focusin", select);
  document.addEventListener("click", select);

  document.addEventListener("click", function (event) {
    var button = event.target && event.target.closest &&
      event.target.closest("[data-copy]");
    if (!button) { return; }
    event.preventDefault();
    var field = document.getElementById(button.getAttribute("data-copy"));
    if (!field) { return; }
    field.select();
    var done = function () {
      var was = button.textContent;
      button.textContent = "Copied";
      setTimeout(function () { button.textContent = was; }, 1500);
    };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(field.value).then(done, function () {});
    } else if (document.execCommand("copy")) {
      done();
    }
  });
})();
