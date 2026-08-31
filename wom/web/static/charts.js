/* The Overview charts, drawn in the browser with D3.
 *
 * Each card owns one chart. Changing a dropdown or a player tick refetches
 * only the JSON behind the affected cards and redraws in place - the page
 * never reloads, and neither does anything else on it.
 */
(function () {
  "use strict";

  var css = getComputedStyle(document.documentElement);
  var COLOR = {
    ink: css.getPropertyValue("--ink").trim() || "#e6e9ed",
    muted: css.getPropertyValue("--muted").trim() || "#99a2ad",
    line: css.getPropertyValue("--line").trim() || "#333941",
    grid: css.getPropertyValue("--grid").trim() || "#39414a",
    panel: css.getPropertyValue("--card").trim() || "#1e2227"
  };

  var WIDE = { top: 34, right: 16, bottom: 46, left: 62 };
  var NARROW = { top: 30, right: 8, bottom: 38, left: 40 };
  var PHONE = 560;                 // below this the desktop margins eat the plot
  var ICON_PX = 22;
  var LEGEND_TOP = 14;             // where legend row 0 sits
  var LEGEND_ROW = 16;

  function marginsFor(width) {
    return width < PHONE ? NARROW : WIDE;
  }
  var full = new Intl.NumberFormat();
  var compact = d3.format("~s");
  var day = d3.timeFormat("%d %b %Y");
  var dayTime = d3.timeFormat("%d %b %H:%M");

  var tip = d3.select("body").append("div").attr("class", "tip").style("opacity", 0);

  /* A touch event carries its position on the touch, not the event. Without
     this every tooltip on a phone would appear in the top-left corner. */
  function pointOf(event) {
    var touch = event.touches && event.touches[0];
    return touch ? {x: touch.clientX, y: touch.clientY}
                 : {x: event.clientX, y: event.clientY};
  }

  function showTip(event, html) {
    tip.html(html).style("opacity", 1);
    var box = tip.node().getBoundingClientRect();
    var at = pointOf(event);
    var x = at.x + 14;
    var y = at.y + 16;
    if (x + box.width > window.innerWidth - 8) { x = at.x - box.width - 14; }
    if (x < 8) { x = 8; }
    if (y + box.height > window.innerHeight - 8) { y = at.y - box.height - 12; }
    tip.style("left", (x + window.scrollX) + "px").style("top", (y + window.scrollY) + "px");
  }

  function hideTip() { tip.style("opacity", 0); }

  /* The tooltip is assembled as HTML, so everything that came from the
     server is escaped on the way in. A display name arrives from the Wise Old
     Man API; if one ever carried markup it would otherwise run here. */
  function escapeHtml(value) {
    return String(value).replace(/[&<>"']/g, function (ch) {
      return {"&": "&amp;", "<": "&lt;", ">": "&gt;",
              '"': "&quot;", "'": "&#39;"}[ch];
    });
  }

  function swatch(color, name) {
    return '<span class="tip-dot" style="background:' +
      escapeHtml(color) + '"></span>' + escapeHtml(name);
  }

  /* -- a card ---------------------------------------------------------- */

  function Chart(node) {
    this.node = node;
    this.key = node.dataset.key;
    this.host = d3.select(node).select(".plot");
    this.data = null;
    this.muted = {};                 // username -> hidden by a legend click
    this.seq = 0;                    // guards against an old reply landing last
    var self = this;
    var pending = null;
    window.addEventListener("resize", function () {
      clearTimeout(pending);
      pending = setTimeout(function () { self.draw(); }, 120);
    });
  }

  Chart.prototype.load = function (query) {
    var self = this;
    var choice = this.node.querySelector("select.choice");
    var url = "/api/chart/" + this.key + "?" + query +
      (choice ? "&choice=" + encodeURIComponent(choice.value) : "");
    var mine = ++this.seq;
    this.host.classed("loading", true);
    return fetch(url).then(function (r) { return r.json(); }).then(function (payload) {
      // Ticking several boxes quickly starts several requests; only the
      // newest one may draw, whatever order the replies come back in.
      if (mine !== self.seq) { return; }
      self.host.classed("loading", false);
      self.data = payload;
      self.draw();
    }).catch(function (err) {
      if (mine !== self.seq) { return; }
      self.host.classed("loading", false);
      self.message("Could not load this chart: " + err);
    });
  };

  Chart.prototype.message = function (text) {
    this.host.html('<p class="empty"></p>');
    this.host.select("p.empty").text(text);
  };

  Chart.prototype.frame = function (height) {
    var width = this.host.node().clientWidth || 900;
    var m = marginsFor(width);
    // A phone gets a shorter card too: the same 360px against a 340px width
    // is a near-square chart, which reads worse than a wide short one.
    if (width < PHONE) { height = Math.round(height * 0.8); }
    this.host.html("");
    var svg = this.host.append("svg")
      .attr("viewBox", "0 0 " + width + " " + height)
      .attr("width", "100%").attr("height", height);
    return { svg: svg, width: width, height: height, m: m, narrow: width < PHONE,
             inner: width - m.left - m.right,
             top: m.top,
             tall: height - m.top - m.bottom };
  };

  Chart.prototype.visible = function () {
    var self = this;
    return this.data.series.filter(function (s) { return !self.muted[s.username]; });
  };

  Chart.prototype.draw = function () {
    if (!this.data) { return; }
    if (this.data.empty) { this.message(this.data.empty); return; }
    if (this.data.type === "standings") { this.standings(); }
    else if (this.data.type === "stacked") { this.stacked(); }
    else { this.trend(); }
  };

  /* Not a chart: the one line per player the columns below make you add up by
     eye. Built as a table because that is what it is. */
  Chart.prototype.standings = function () {
    var host = this.host;
    host.html("");
    var table = host.append("table").attr("class", "standings");
    var head = table.append("tr");
    ["", "Player", "XP gained", "Levels", "Boss kills"].forEach(function (label, i) {
      head.append("th").attr("class", i > 1 ? "num" : null).text(label);
    });
    this.data.rows.forEach(function (row, index) {
      var tr = table.append("tr").attr("class", index === 0 ? "leader" : null);
      tr.append("td").attr("class", "rank").text(index + 1);
      var name = tr.append("td").attr("class", "name");
      name.append("span").attr("class", "swatch")
        .style("background", row.color).style("display", "inline-block")
        .style("margin-right", "7px");
      name.append("span").text(row.name);
      tr.append("td").attr("class", "num").text(full.format(row.xp));
      tr.append("td").attr("class", "num dim")
        .text(row.levels ? "+" + row.levels : "");
      tr.append("td").attr("class", "num dim")
        .text(row.kills ? full.format(row.kills) : "");
    });
    if (this.data.coverage && this.data.coverage.length) {
      this.coverage(this.data.rows);
    }
  };

  /* -- axes ------------------------------------------------------------ */

  function valueAxis(g, f, scale, label) {
    var inner = f.inner;
    var axis = d3.axisLeft(scale).ticks(f.narrow ? 4 : 6).tickFormat(function (v) {
      return Math.abs(v) >= 1000 ? compact(v) : full.format(v);
    });
    var drawn = g.append("g").call(axis);
    drawn.select(".domain").remove();
    drawn.selectAll("text").attr("fill", COLOR.muted).style("font-size", "11px");
    drawn.selectAll("line").attr("stroke", COLOR.line);
    // Gridlines behind everything, drawn from the same ticks.
    g.insert("g", ":first-child").selectAll("line")
      .data(scale.ticks(f.narrow ? 4 : 6)).join("line")
      .attr("x1", 0).attr("x2", inner)
      .attr("y1", scale).attr("y2", scale)
      .attr("stroke", COLOR.grid).attr("stroke-opacity", 0.55);
    // The axis label costs horizontal room a phone has not got, and the tick
    // numbers already say what the units are.
    if (label && !f.narrow) {
      g.append("text").attr("transform", "rotate(-90)")
        .attr("x", -(scale.range()[0] / 2)).attr("y", -f.m.left + 14)
        .attr("fill", COLOR.muted).attr("text-anchor", "middle")
        .style("font-size", "11px").text(label);
    }
  }

  Chart.prototype.legend = function (svg, f) {
    var self = this;
    var size = f.narrow ? 11 : 12;
    var gap = f.narrow ? 12 : 18;
    var row = svg.append("g")
      .attr("transform", "translate(" + f.m.left + "," + LEGEND_TOP + ")");
    var limit = f.inner;
    var x = 0;
    var line = 0;                    // which legend row we are filling
    this.data.series.forEach(function (s) {
      var off = !!self.muted[s.username];
      var item = row.append("g")
        .attr("transform", "translate(" + x + "," + (line * LEGEND_ROW) + ")")
        .style("cursor", "pointer").attr("opacity", off ? 0.4 : 1);
      item.append("rect").attr("width", 10).attr("height", 10).attr("rx", 2)
        .attr("y", -8).attr("fill", off ? COLOR.muted : s.color);
      item.append("text").attr("x", 15).attr("fill", COLOR.ink)
        .style("font-size", size + "px").text(s.name);
      var span = item.node().getBBox().width + gap;
      // Wrap onto the next row rather than run off the card. `line` has to
      // carry: resetting only x would drop the entries after this one back
      // onto the row above, on top of the ones already there.
      if (x + span > limit && x > 0) {
        x = 0;
        line += 1;
        item.attr("transform", "translate(0," + (line * LEGEND_ROW) + ")");
      }
      x += span;
      item.on("click", function () {
        self.muted[s.username] = !self.muted[s.username];
        self.draw();
      }).on("mouseenter mousemove touchstart touchmove", function (event) {
        showTip(event, swatch(s.color, s.name) +
          '<div class="tip-sub">' + (off ? "click to show" : "click to hide") + "</div>");
      }).on("mouseleave", hideTip);
    });
    return line + 1;
  };

  /* Give the legend the vertical room it actually took, measured rather than
     assumed: the narrow margins are tight enough that even a second row would
     otherwise be drawn across the top of the plot. */
  function makeRoom(f, rows) {
    var needed = LEGEND_TOP + rows * LEGEND_ROW + 4;
    f.top = Math.max(f.m.top, needed);
    f.tall = f.height - f.top - f.m.bottom;
    return f;
  }

  /* -- stacked columns ------------------------------------------------- */

  Chart.prototype.stacked = function () {
    var data = this.data;
    var shown = this.visible();
    var f = this.frame(360);
    var svg = f.svg;
    makeRoom(f, this.legend(svg, f));
    this.coverage(shown);

    var x = d3.scaleBand().domain(d3.range(data.metrics.length))
      .range([0, f.inner]).padding(0.22);
    var totals = data.metrics.map(function (_m, i) {
      return d3.sum(shown, function (s) { return s.values[i]; });
    });
    var y = d3.scaleLinear().domain([0, d3.max(totals) || 1]).nice()
      .range([f.tall, 0]);

    var g = svg.append("g")
      .attr("transform", "translate(" + f.m.left + "," + f.top + ")");
    valueAxis(g, f, y, data.ylabel);

    var bottoms = data.metrics.map(function () { return 0; });
    shown.forEach(function (s) {
      g.append("g").selectAll("rect")
        .data(s.values.map(function (v, i) {
          var base = bottoms[i];
          bottoms[i] += v;
          return { i: i, v: v, base: base };
        }).filter(function (d) { return d.v > 0; }))
        .join("rect")
        .attr("x", function (d) { return x(d.i); })
        .attr("width", x.bandwidth())
        .attr("y", function (d) { return y(d.base + d.v); })
        .attr("height", function (d) { return Math.max(1, y(d.base) - y(d.base + d.v)); })
        .attr("fill", s.color)
        .attr("stroke", COLOR.panel).attr("stroke-width", 0.5)
        .on("mouseenter mousemove touchstart touchmove", function (event, d) {
          showTip(event, swatch(s.color, s.name) + '<div class="tip-sub">' +
            escapeHtml(data.metrics[d.i].label) + ": " +
            full.format(Math.round(d.v)) + " " + escapeHtml(data.unit) +
            "</div>");
        })
        .on("mouseleave", hideTip);
    });

    // Icons stand in for the x tick labels, exactly as on the desktop tab.
    var slot = Math.min(ICON_PX, x.bandwidth());
    var axis = g.append("g").attr("transform", "translate(0," + f.tall + ")");
    axis.append("line").attr("x2", f.inner).attr("stroke", COLOR.line);
    data.metrics.forEach(function (m, i) {
      var cx = x(i) + x.bandwidth() / 2;
      var cell = axis.append("g").attr("transform", "translate(" + cx + ",6)");
      cell.append("image")
        .attr("href", "/icon/" + data.iconKind + "/" + m.key + ".png")
        .attr("x", -slot / 2).attr("width", slot).attr("height", slot)
        .attr("preserveAspectRatio", "xMidYMid meet")
        .style("image-rendering", "pixelated")
        .on("error", function () {
          // No sprite for this metric: fall back to a clipped name.
          d3.select(this).remove();
          cell.append("text").attr("y", 10).attr("text-anchor", "middle")
            .attr("fill", COLOR.muted).style("font-size", "9px")
            .text(m.label.slice(0, 8));
        });
      cell.append("rect").attr("x", -x.bandwidth() / 2).attr("y", -6)
        .attr("width", x.bandwidth()).attr("height", slot + 12)
        .attr("fill", "transparent")
        .on("mouseenter mousemove touchstart touchmove", function (event) {
          var per = shown.map(function (s) { return { s: s, v: s.values[i] }; })
            .filter(function (d) { return d.v > 0; })
            .sort(function (a, b) { return b.v - a.v; });
          var html = "<b></b>";
          var head = d3.create("b");
          head.text(m.label);
          html = head.node().outerHTML;
          per.forEach(function (d) {
            html += '<div class="tip-sub">' + swatch(d.s.color, d.s.name) +
              " &middot; " + full.format(Math.round(d.v)) + "</div>";
          });
          if (!per.length) { html += '<div class="tip-sub">nothing this period</div>'; }
          showTip(event, html);
        })
        .on("mouseleave", hideTip);
    });
  };

  /* A bar can only cover the history Wise Old Man actually has. Say so,
     rather than letting three weeks stand next to a full year unremarked. */
  Chart.prototype.coverage = function (shown) {
    var visible = {};
    shown.forEach(function (s) { visible[s.name] = true; });
    var notes = (this.data.coverage || []).filter(function (n) { return visible[n.name]; });
    if (!notes.length) { return; }
    var box = this.host.append("p").attr("class", "coverage");
    box.append("span").text("Short history: ");
    notes.forEach(function (n, i) {
      if (i) { box.append("span").text(", "); }
      box.append("span").attr("class", "tip-dot").style("background", n.color);
      box.append("span").text(n.name + " from " + n.since + " (" + n.days + "d)");
    });
  };

  /* -- trend lines ----------------------------------------------------- */

  Chart.prototype.trend = function () {
    var data = this.data;
    var shown = this.visible();
    var f = this.frame(330);
    var svg = f.svg;
    makeRoom(f, this.legend(svg, f));

    if (!shown.length) { return; }
    var x = d3.scaleUtc().domain([new Date(data.since), new Date()])
      .range([0, f.inner]);
    // Scale to what the window actually shows: an old baseline reading can
    // sit far below the window and would otherwise flatten the whole line.
    var values = [];
    shown.forEach(function (s) {
      s.points.forEach(function (p) {
        if (p[0] >= data.since) { values.push(p[1]); }
      });
    });
    if (!values.length) {
      shown.forEach(function (s) {
        s.points.forEach(function (p) { values.push(p[1]); });
      });
    }
    var lo = d3.min(values), hi = d3.max(values);
    if (lo === hi) { lo -= 1; hi += 1; }
    var y = d3.scaleLinear().domain([lo, hi]).nice().range([f.tall, 0]);

    var g = svg.append("g")
      .attr("transform", "translate(" + f.m.left + "," + f.top + ")");
    valueAxis(g, f, y, data.ylabel);

    var span = (Date.now() - data.since) / 86400000;
    var ticks = g.append("g").attr("transform", "translate(0," + f.tall + ")")
      .call(d3.axisBottom(x).ticks(Math.min(8, Math.max(3, Math.round(f.inner / 110))))
        .tickFormat(span <= 2 ? d3.utcFormat("%H:%M") : d3.utcFormat("%d %b")));
    ticks.select(".domain").attr("stroke", COLOR.line);
    ticks.selectAll("line").attr("stroke", COLOR.line);
    ticks.selectAll("text").attr("fill", COLOR.muted).style("font-size", "11px");

    // The reading each line starts from deliberately sits before the window,
    // so the lines are clipped to the plot rather than running off the card.
    var clip = "clip-" + this.key;
    svg.append("clipPath").attr("id", clip).append("rect")
      .attr("width", f.inner).attr("height", f.tall);
    var plot = g.append("g").attr("clip-path", "url(#" + clip + ")");

    var line = d3.line()
      .x(function (p) { return x(p[0]); })
      .y(function (p) { return y(p[1]); });
    // Wise Old Man's history has holes - weeks or months with no snapshot at
    // all. Joining across one draws a straight line through time nobody
    // measured, which reads as steady progress that may never have happened.
    // Those stretches are dashed: the two ends are real, the middle is a guess.
    var gapLimit = Math.max(1.5 * 86400000, (Date.now() - data.since) * 0.04);
    shown.forEach(function (s) {
      var run = [s.points[0]];
      for (var i = 1; i < s.points.length; i++) {
        var previous = s.points[i - 1];
        var point = s.points[i];
        if (point[0] - previous[0] > gapLimit) {
          stroke(run, false);
          stroke([previous, point], true);
          run = [point];
        } else {
          run.push(point);
        }
      }
      stroke(run, false);
      if (s.points.length < 80) {
        plot.append("g").selectAll("circle").data(s.points).join("circle")
          .attr("cx", function (p) { return x(p[0]); })
          .attr("cy", function (p) { return y(p[1]); })
          .attr("r", 2.2).attr("fill", s.color);
      }

      function stroke(points, guessed) {
        if (points.length < 2) { return; }
        plot.append("path").datum(points).attr("fill", "none")
          .attr("stroke", s.color).attr("stroke-width", guessed ? 1.2 : 1.8)
          .attr("stroke-opacity", guessed ? 0.5 : 1)
          .attr("stroke-dasharray", guessed ? "4,4" : null)
          .attr("stroke-linejoin", "round").attr("d", line);
      }
    });

    // One crosshair for every line, so the players can be read off together.
    var rule = g.append("line").attr("y1", 0).attr("y2", f.tall)
      .attr("stroke", COLOR.muted).attr("stroke-dasharray", "3,3").style("opacity", 0);
    var dots = plot.append("g");
    var fmt = data.tooltip || { style: "count", unit: "" };

    g.append("rect").attr("width", f.inner).attr("height", f.tall)
      .attr("fill", "transparent")
      .on("mouseleave", function () {
        rule.style("opacity", 0); dots.selectAll("*").remove(); hideTip();
      })
      .on("mousemove touchstart touchmove", function (event) {
        var at = x.invert(d3.pointer(event, this)[0]).getTime();
        var picks = [];
        shown.forEach(function (s) {
          var index = d3.leastIndex(s.points, function (a, b) {
            return Math.abs(a[0] - at) - Math.abs(b[0] - at);
          });
          if (index != null && index >= 0) { picks.push({ s: s, p: s.points[index] }); }
        });
        if (!picks.length) { return; }
        var anchor = picks.reduce(function (best, d) {
          return Math.abs(d.p[0] - at) < Math.abs(best.p[0] - at) ? d : best;
        });
        rule.attr("x1", x(anchor.p[0])).attr("x2", x(anchor.p[0])).style("opacity", 0.7);
        dots.selectAll("circle").data(picks).join("circle")
          .attr("cx", function (d) { return x(d.p[0]); })
          .attr("cy", function (d) { return y(d.p[1]); })
          .attr("r", 4).attr("fill", function (d) { return d.s.color; })
          .attr("stroke", COLOR.panel).attr("stroke-width", 1.5);
        picks.sort(function (a, b) { return b.p[1] - a.p[1]; });
        var stamp = new Date(anchor.p[0]);
        var head = d3.create("b");
        head.text(span <= 2 ? dayTime(stamp) : day(stamp));
        var html = head.node().outerHTML;
        picks.forEach(function (d) {
          var value = fmt.style === "level"
            ? "level " + full.format(d.p[1]) + " (" + full.format(d.p[2]) + " XP)"
            : full.format(d.p[1]) + " " + escapeHtml(fmt.unit || "");
          html += '<div class="tip-sub">' + swatch(d.s.color, d.s.name) +
            " &middot; " + value + "</div>";
        });
        showTip(event, html);
      });
  };

  /* -- the page -------------------------------------------------------- */

  var charts = [];

  function query() {
    var params = new URLSearchParams();
    params.set("period", document.getElementById("period").value);
    // Says the ticks below are a real choice, so unticking everyone means
    // nobody rather than a bare link's "show me all of them".
    params.set("picked", "1");
    document.querySelectorAll("input[name=player]:checked").forEach(function (box) {
      params.append("player", box.value);
    });
    return params.toString();
  }

  var queued = null;

  function loadAll() {
    // Ticking a run of boxes should cost one round of requests, not one per
    // box, so let the clicks settle first.
    clearTimeout(queued);
    queued = setTimeout(function () {
      var q = query();
      // Keep the address bar in step, so a view can be linked or reloaded.
      history.replaceState(null, "", "/?" + q);
      charts.forEach(function (chart) { chart.load(q); });
    }, 90);
  }

  // A finger has no "mouseleave", so a tap anywhere else puts the tip away.
  document.addEventListener("touchstart", function (event) {
    if (!event.target.closest || !event.target.closest(".plot")) { hideTip(); }
  }, {passive: true});

  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll(".card.chart").forEach(function (node) {
      charts.push(new Chart(node));
    });
    document.getElementById("period").addEventListener("change", loadAll);
    document.querySelectorAll("input[name=player]").forEach(function (box) {
      box.addEventListener("change", loadAll);
    });
    document.querySelectorAll("select.choice").forEach(function (select) {
      select.addEventListener("change", function () {
        var card = select.closest(".card.chart");
        charts.filter(function (c) { return c.node === card; })
          .forEach(function (c) { c.load(query()); });
      });
    });
    loadAll();
  });
})();
