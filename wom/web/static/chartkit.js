/* The chart machinery: one Chart per card, drawn in the browser with D3.
 *
 * A card owns one chart. Changing a control refetches only the JSON behind
 * the affected cards and redraws in place - the page never reloads, and
 * neither does anything else on it.
 *
 * This file knows nothing about any particular page. The Overview drives it
 * from overview.js and the Data page from table.js; both reach it through
 * the window.WOM handle at the bottom.
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
  /* Headline figures, which are arbitrary rather than the round numbers an
     axis tick lands on: "~s" alone carries six significant digits, so a
     group total reads 76.1571M where the point of the tile is 76.2M. */
  var tight = d3.format(".3~s");
  /* Time labels are written in the zone a chart's window is defined in, and
     a payload says which by sending `offset` (minutes east of UTC). Where it
     says nothing, the viewer's own zone is the honest default - the sidebar's
     dates are already read that way.

     Formatting is done in UTC against a shifted timestamp, which is the only
     way to render a zone the browser is not in. It also settles a
     disagreement: the axis used to be written in UTC and the tooltip in the
     viewer's zone, so hovering a point named an hour the tick beneath it
     contradicted. Both go through here now. */
  var day = d3.utcFormat("%d %b %Y");
  var dayTime = d3.utcFormat("%d %b %H:%M");

  function labelZone(data) {
    var offset = (data && data.offset !== undefined && data.offset !== null)
      ? data.offset : -new Date().getTimezoneOffset();
    return function (stamp) {
      return new Date((stamp instanceof Date ? stamp.getTime() : stamp)
                      + offset * 60000);
    };
  }

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
    /* Which reading of this card is showing, for a card that offers more than
       one. Taken from the markup rather than assumed, so the default lives in
       one place - the first entry of the spec's modes. Null on every card
       that has no modes, which is all of them but the two trends. */
    var pressed = node.querySelector("button.mode[aria-pressed='true']");
    this.mode = pressed ? pressed.dataset.mode : null;
    var self = this;
    var pending = null;
    window.addEventListener("resize", function () {
      clearTimeout(pending);
      pending = setTimeout(function () { self.draw(); }, 120);
    });
  }

  /* Where this card's JSON comes from. The default is the Overview's chart
     endpoint keyed by the card; a page with one chart of its own overrides
     it rather than pretending to be a catalogue entry. */
  Chart.prototype.endpoint = function (query) {
    var choice = this.node.querySelector("select.choice");
    return "/api/chart/" + this.key + "?" + query +
      (choice ? "&choice=" + encodeURIComponent(choice.value) : "");
  };

  Chart.prototype.load = function (query) {
    var self = this;
    var url = this.endpoint(query);
    var mine = ++this.seq;
    this.host.classed("loading", true);
    return fetch(url).then(function (r) { return r.json(); }).then(function (payload) {
      // Ticking several boxes quickly starts several requests; only the
      // newest one may draw, whatever order the replies come back in.
      if (mine !== self.seq) { return; }
      self.host.classed("loading", false);
      // Every answer says what window it answered over; the sidebar's dates
      // show it, so a preset never leaves them reading something else.
      if (window.Sidebar && payload.span) { window.Sidebar.showWindow(payload.span); }
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

  Chart.prototype.frame = function (height, extra) {
    var width = this.host.node().clientWidth || 900;
    var m = marginsFor(width);
    if (extra) {
      // Copied, not edited: marginsFor hands back the one shared WIDE/NARROW
      // object, and a chart that needed more room would take it from all of them.
      var copy = {top: m.top, right: m.right, bottom: m.bottom, left: m.left};
      for (var side in extra) { copy[side] = extra[side]; }
      m = copy;
    }
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

  /* A mode is a way of reading what has already arrived, not a different
     question, so it redraws rather than refetching. */
  Chart.prototype.setMode = function (mode) {
    this.mode = mode;
    this.draw();
  };

  /* Which of the two readings bracketing the window's opening a gain is
     measured from: whichever sits nearer to it. The same rule
     winners.opening_reading and db.baseline_snapshot follow on the server,
     so a card measures a period the way the rest of the site does.

     Taking the one before unconditionally reads as obviously right - it is
     the reading the window opens on, and the server sends it so a line can
     start at the left edge. But Wise Old Man's history has holes, and
     "before" can be years before: an account last seen in 2022 and next seen
     inside the window had four years of progress counted as this month's.
     Wrong on its own, and being enormous it stretched the axis and squashed
     every well-covered account's real month into the floor of the card.

     Both horns need handling, which is why it is this rule and not "measure
     from the first point in the window" - an account whose history begins
     mid-window would then report its own late start as a gain of zero. */
  function openingReading(points, since) {
    var before = null, inside = null;
    for (var i = 0; i < points.length; i++) {
      if (points[i][0] <= since) { before = points[i]; }
      else { inside = points[i]; break; }
    }
    if (before === null) { return inside; }
    if (inside === null) { return before; }
    return (inside[0] - since) < (since - before[0]) ? inside : before;
  }

  /* The same series expressed as the change since that reading. */
  function rebased(series, since) {
    return series.map(function (s) {
      var opening = openingReading(s.points, since);
      if (opening === null) { return s; }
      var base = opening[1];
      return {
        username: s.username, name: s.name, color: s.color,
        // Readings before the one a gain is measured from would draw a
        // negative tail into the window, so the line starts where the
        // measurement does.
        points: s.points.filter(function (p) { return p[0] >= opening[0]; })
          .map(function (p) { return [p[0], p[1] - base, p[2]]; })
      };
    });
  }

  Chart.prototype.draw = function () {
    if (!this.data) { return; }
    if (this.data.empty) { this.message(this.data.empty); return; }
    if (this.data.type === "totals") { this.totals(); }
    else if (this.data.type === "standings") { this.standings(); }
    else if (this.data.type === "stacked") { this.stacked(); }
    else { this.trend(); }
  };

  /* Not a chart either: six figures about the whole group, each carrying the
     per-account split on hover. A button rather than a div, because it holds
     something you have to be able to reach without a mouse. */
  Chart.prototype.totals = function () {
    var host = this.host;
    host.html("");
    var grid = host.append("div").attr("class", "totals");

    this.data.tiles.forEach(function (tile) {
      var shown = tile.format === "compact" ? tight(tile.total)
                                            : full.format(tile.total);
      var cell = grid.append("button")
        .attr("type", "button").attr("class", "total")
        .attr("aria-label", tile.label + ": " + full.format(tile.total));
      cell.append("span").attr("class", "total-key").text(tile.label);
      cell.append("span").attr("class", "total-value").text(shown);
      cell.append("span").attr("class", "total-note").text(tile.note);

      /* The split is built on demand rather than up front: six tiles times
         however many accounts is a lot of markup for something most of which
         is never looked at. */
      var describe = function (event) {
        var head = d3.create("b");
        head.text(tile.label);
        var html = head.node().outerHTML;
        tile.rows.forEach(function (row) {
          var value = tile.format === "compact" ? tight(row.value)
                                                : full.format(row.value);
          html += '<div class="tip-sub">' + swatch(row.color, row.name) +
            " &middot; " + value + "</div>";
        });
        showTip(event, html);
      };
      cell.on("mousemove touchstart", describe)
        .on("mouseleave", hideTip)
        .on("focus", function (event) { describe(event); })
        .on("blur", hideTip);
    });

    if (this.data.coverage && this.data.coverage.length) {
      this.coverage(this.data.tiles[0].rows);
    }
  };

  /* Not a chart: the one line per player the columns below make you add up by
     eye. Built as a table because that is what it is. */
  Chart.prototype.standings = function () {
    var host = this.host;
    host.html("");
    /* The same six measures as the group tiles above, in an order that keeps
       XP gained first because the table is sorted by it, and XP toward 99
       beside it because the gap between the two is why both are shown.

       A zero reads as blank rather than "0" everywhere but the sorted column:
       seven columns of noughts is a wall to read past, and the absence is the
       information. XP gained keeps its nought so the sort stays legible. */
    var table = host.append("table").attr("class", "standings");
    var head = table.append("tr");
    ["", "Player", "XP gained", "XP toward 99", "Levels", "Boss kills",
     "Clog", "Clues"].forEach(function (label, i) {
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
      [["xp99", false], ["levels", true], ["kills", false],
       ["collections", false], ["clues", false]].forEach(function (pair) {
        var value = row[pair[0]];
        tr.append("td").attr("class", "num dim")
          .text(value ? (pair[1] ? "+" + value : full.format(value)) : "");
      });
    });
    if (this.data.coverage && this.data.coverage.length) {
      this.coverage(this.data.rows);
    }
  };

  /* -- axes ------------------------------------------------------------ */

  /* The Old School experience curve: LEVEL_XP[n] is the experience a skill
     needs for level n. A level is a fixed function of experience, the same
     for every account, so an axis of levels beside one of experience is exact
     rather than an approximation. It stops at 99, which is where the game
     stops counting and where the levels this app stores stop too. */
  var LEVEL_XP = (function () {
    var table = [null, 0];
    var points = 0;
    for (var lvl = 1; lvl < 99; lvl++) {
      points += Math.floor(lvl + 300 * Math.pow(2, lvl / 7));
      table.push(Math.floor(points / 4));
    }
    return table;
  })();

  /* Which levels to rule the plot at. Every fifth, as the coarse reading;
     but a week of one skill can span less than five levels, so fall back to
     every level rather than draw a chart with one line across it. A window
     living entirely above 99 has no level boundaries in it at all, and gets
     the plain experience axis instead. */
  function levelTicks(domain) {
    var steps = [5, 1];
    for (var i = 0; i < steps.length; i++) {
      var found = [];
      for (var lvl = 1; lvl <= 99; lvl++) {
        if (lvl % steps[i]) { continue; }
        if (LEVEL_XP[lvl] >= domain[0] && LEVEL_XP[lvl] <= domain[1]) {
          found.push(lvl);
        }
      }
      if (found.length >= 2) { return found; }
    }
    return null;
  }

  /* Levels on the left with the gridlines, experience on the right with
     none: two rulings over one plot is a grid nobody can read a value off. */
  function levelAxis(g, f, scale, levels) {
    // Experience per level grows exponentially, so on a plot that reaches
    // down to a low level the bottom ten of them share a few pixels and the
    // numbers print over each other. Thin them by distance on screen: every
    // line that survives is still exactly a level boundary.
    var kept = [];
    var at = [];
    var last = null;
    levels.forEach(function (lvl) {
      var y = scale(LEVEL_XP[lvl]);
      if (last !== null && Math.abs(last - y) < 14) { return; }
      last = y;
      kept.push(lvl);
      at.push(LEVEL_XP[lvl]);
    });
    var left = g.append("g").call(
      d3.axisLeft(scale).tickValues(at).tickFormat(function (_v, i) {
        return kept[i];
      }));
    left.select(".domain").remove();
    left.selectAll("text").attr("fill", COLOR.muted).style("font-size", "11px");
    left.selectAll("line").attr("stroke", COLOR.line);
    g.insert("g", ":first-child").selectAll("line").data(at).join("line")
      .attr("x1", 0).attr("x2", f.inner)
      .attr("y1", scale).attr("y2", scale)
      .attr("stroke", COLOR.grid).attr("stroke-opacity", 0.55);

    var right = g.append("g")
      .attr("transform", "translate(" + f.inner + ",0)")
      .call(d3.axisRight(scale).ticks(f.narrow ? 4 : 6).tickFormat(compact));
    right.select(".domain").remove();
    right.selectAll("text").attr("fill", COLOR.muted).style("font-size", "11px");
    right.selectAll("line").attr("stroke", COLOR.line);

    if (!f.narrow) {
      sideLabel(g, f, scale, "Level", -f.m.left + 14);
      sideLabel(g, f, scale, "Experience", f.inner + f.m.right - 6);
    }
  }

  function sideLabel(g, f, scale, text, y) {
    g.append("text").attr("transform", "rotate(-90)")
      .attr("x", -(scale.range()[0] / 2)).attr("y", y)
      .attr("fill", COLOR.muted).attr("text-anchor", "middle")
      .style("font-size", "11px").text(text);
  }

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

    // Icons stand in for the x tick labels, as the hiscore panel does.
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
    /* "Gained" plots the movement rather than the value. Everything below
       reads `shown`, so rebasing here is the whole of the difference - bar
       the axis, which stops being a scale of levels once it is a scale of
       changes in them. */
    var gained = this.mode === "Gained";
    var shown = this.visible();
    if (gained) { shown = rebased(shown, data.since); }

    /* The vertical extent is settled before the frame is, because whether
       there is a second axis decides how much room the right margin needs -
       and levelAxis can be asked for and still not be drawable, when the
       whole window sits above level 99 and holds no level boundary. Reserving
       the room first left an empty gutter in exactly that case.

       Scale to what the window actually shows: an old baseline reading can
       sit far below it and would otherwise flatten the whole line. */
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
    /* A change is read against nothing having happened, so zero belongs on
       the axis whether or not anything sits there. It does not arrive on its
       own: the point holding it is the baseline, which sits before `since`
       and is exactly what the extent above filters out - so a card of gains
       between +5 and +22 drew an axis starting at 5 and clipped the opening
       of every line below it. */
    if (gained) { lo = Math.min(0, lo); hi = Math.max(0, hi); }
    if (lo === hi) { lo -= 1; hi += 1; }
    var domain = d3.scaleLinear().domain([lo, hi]).nice().domain();
    // A level axis maps experience to the level it buys, which is a statement
    // about a total. A difference of experience does not sit anywhere on it.
    var levels = (data.levelAxis && !gained && shown.length)
      ? levelTicks(domain) : null;

    var f = this.frame(330, levels ? {right: 52} : null);
    var svg = f.svg;
    makeRoom(f, this.legend(svg, f));

    if (!shown.length) { return; }
    // A window with a chosen end date stops there; one that is still running
    // stops now.
    var ends = data.until || Date.now();
    var x = d3.scaleUtc().domain([new Date(data.since), new Date(ends)])
      .range([0, f.inner]);
    var y = d3.scaleLinear().domain(domain).range([f.tall, 0]);

    var g = svg.append("g")
      .attr("transform", "translate(" + f.m.left + "," + f.top + ")");
    // For a single skill the left axis reads in levels and the right in
    // experience. The line is still drawn on experience: it is what moves
    // continuously, and a line of levels is a staircase that hides a week's
    // work inside one step.
    if (levels) { levelAxis(g, f, y, levels); }
    else { valueAxis(g, f, y, gained ? data.ylabelGained : data.ylabel); }

    var span = (ends - data.since) / 86400000;
    var inZone = labelZone(data);
    var tickFmt = span <= 2 ? d3.utcFormat("%H:%M") : d3.utcFormat("%d %b");
    var ticks = g.append("g").attr("transform", "translate(0," + f.tall + ")")
      .call(d3.axisBottom(x).ticks(Math.min(8, Math.max(3, Math.round(f.inner / 110))))
        .tickFormat(function (at) { return tickFmt(inZone(at)); }));
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
    var gapLimit = Math.max(1.5 * 86400000, (ends - data.since) * 0.04);
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
    var fmt = (gained ? data.tooltipGained : data.tooltip)
      || { style: "count", unit: "" };

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
        var stamp = inZone(anchor.p[0]);
        var head = d3.create("b");
        head.text(span <= 2 ? dayTime(stamp) : day(stamp));
        var html = head.node().outerHTML;
        picks.forEach(function (d) {
          var value;
          if (gained) {
            // A signed figure, because zero and "nothing yet" look the same
            // on a line that starts at zero and the sign is what tells them
            // apart at a glance.
            value = (d.p[1] > 0 ? "+" : "") + full.format(d.p[1]) +
              " " + escapeHtml(fmt.unit || "");
          } else {
            value = fmt.style === "level"
              ? "level " + full.format(d.p[1]) + " (" + full.format(d.p[2]) + " XP)"
              : full.format(d.p[1]) + " " + escapeHtml(fmt.unit || "");
          }
          html += '<div class="tip-sub">' + swatch(d.s.color, d.s.name) +
            " &middot; " + value + "</div>";
        });
        showTip(event, html);
      });
  };

  /* -- what a page may use --------------------------------------------- */

  // Added to whatever is already there rather than replacing it: store.js
  // loads first and puts WOM.Remember on the same handle.
  window.WOM = window.WOM || {};
  window.WOM.Chart = Chart;
  window.WOM.hideTip = hideTip;
  window.WOM.escapeHtml = escapeHtml;

  // A finger has no "mouseleave", so a tap anywhere else puts the tip away.
  document.addEventListener("touchstart", function (event) {
    if (!event.target.closest || !event.target.closest(".plot")) { hideTip(); }
  }, {passive: true});
})();
