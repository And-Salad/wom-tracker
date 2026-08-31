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
