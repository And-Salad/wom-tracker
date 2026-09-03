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
(function () {
  var fields = document.querySelectorAll(".secret");
  Array.prototype.forEach.call(fields, function (field) {
    field.addEventListener("focus", function () { field.select(); });
    field.addEventListener("click", function () { field.select(); });
  });

  var buttons = document.querySelectorAll("[data-copy]");
  Array.prototype.forEach.call(buttons, function (button) {
    button.addEventListener("click", function (event) {
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
  });
})();
