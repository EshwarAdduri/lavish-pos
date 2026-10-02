/* Draws the dashboard / report charts from the JSON the page embeds. Re-colours on theme change. */
(function () {
  "use strict";
  var charts = [];
  function css(name) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); }
  function palette() {
    return { brand: css("--brand"), ink: css("--ink"), muted: css("--muted"), line: css("--line"), ok: css("--ok"),
      info: css("--info"), warn: css("--saffron") || "#f59f00", bad: css("--bad") };
  }
  function money(v) { return window.inr ? window.inr(v) : "₹" + v; }

  function build() {
    var el = document.getElementById("chart-data");
    if (!el || !window.Chart) return;
    var d = JSON.parse(el.textContent);
    var p = palette();
    charts.forEach(function (c) { c.destroy(); });
    charts = [];
    Chart.defaults.color = p.muted;
    Chart.defaults.font.family = getComputedStyle(document.body).fontFamily;
    Chart.defaults.borderColor = p.line;
    var tip = { callbacks: { label: function (ctx) { var v = ctx.parsed.y !== undefined ? ctx.parsed.y : ctx.parsed; return " " + (ctx.dataset.isCount ? v : money(v)); } } };
    var noLegend = { legend: { display: false }, tooltip: tip };
    var yMoney = { beginAtZero: true, ticks: { callback: function (v) { return v >= 1000 ? "₹" + (v / 1000) + "k" : "₹" + v; } }, grid: { color: p.line } };

    function make(id, cfg) { var c = document.getElementById(id); if (c) charts.push(new Chart(c, cfg)); }

    make("ch-daily", { type: "bar", data: { labels: d.daily.labels, datasets: [{ label: "Sales", data: d.daily.sales, backgroundColor: p.brand, borderRadius: 6, maxBarThickness: 36 }] },
      options: { maintainAspectRatio: false, plugins: noLegend, scales: { y: yMoney, x: { grid: { display: false } } } } });
    var hours = d.hour.labels.map(function (h, i) { return { h: h, v: d.hour.sales[i] }; });
    var first = hours.findIndex(function (x) { return x.v > 0; }), last = hours.length - 1 - hours.slice().reverse().findIndex(function (x) { return x.v > 0; });
    if (first === -1) { first = 10; last = 23; }
    hours = hours.slice(Math.max(0, first - 1), Math.min(24, last + 2));
    make("ch-hour", { type: "bar", data: { labels: hours.map(function (x) { var n = +x.h; return (n % 12 || 12) + (n < 12 ? "am" : "pm"); }), datasets: [{ data: hours.map(function (x) { return x.v; }), backgroundColor: p.warn, borderRadius: 5 }] },
      options: { maintainAspectRatio: false, plugins: noLegend, scales: { y: yMoney, x: { grid: { display: false } } } } });
    make("ch-weekday", { type: "bar", data: { labels: d.weekday.labels, datasets: [{ data: d.weekday.sales, backgroundColor: p.info, borderRadius: 5 }] },
      options: { maintainAspectRatio: false, plugins: noLegend, scales: { y: yMoney, x: { grid: { display: false } } } } });
    var donut = function (id, set) {
      make(id, { type: "doughnut", data: { labels: set.labels, datasets: [{ data: set.values, backgroundColor: [p.ok, p.brand, p.info, p.warn, p.bad, p.muted], borderWidth: 0 }] },
        options: { maintainAspectRatio: false, cutout: "62%", plugins: { legend: { position: "bottom", labels: { boxWidth: 10, usePointStyle: true } }, tooltip: tip } } });
    };
    if (d.methods) donut("ch-methods", d.methods);
    if (d.types) donut("ch-types", d.types);
  }
  document.addEventListener("DOMContentLoaded", build);
  new MutationObserver(function () { setTimeout(build, 30); })
    .observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
})();
