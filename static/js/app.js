/* Lavish Shawarma — shared page behaviour: theme, toasts, live sync, app install. */
(function () {
  "use strict";

  // ------------------------------------------------------------ theme
  function setTheme(t) {
    document.documentElement.setAttribute("data-theme", t);
    try { localStorage.setItem("ls-theme", t); } catch (e) {}
  }
  document.addEventListener("click", function (e) {
    var btn = e.target.closest("[data-theme-toggle]");
    if (!btn) return;
    var cur = document.documentElement.getAttribute("data-theme");
    setTheme(cur === "dark" ? "light" : "dark");
  });

  // ------------------------------------------------------------ toasts
  window.toast = function (text, kind) {
    var box = document.getElementById("toasts");
    if (!box) return;
    var el = document.createElement("div");
    el.className = "toast toast-" + (kind || "info");
    el.setAttribute("data-autohide", "");
    el.textContent = text;
    box.appendChild(el);
    hideLater(el);
  };
  function hideLater(el) {
    setTimeout(function () {
      el.style.transition = "opacity .3s"; el.style.opacity = "0";
      setTimeout(function () { el.remove(); }, 300);
    }, el.classList.contains("toast-error") || el.classList.contains("toast-bad") ? 6000 : 3500);
  }
  function wireToasts(root) {
    (root || document).querySelectorAll("[data-autohide]").forEach(function (el) {
      if (el.dataset.wired) return;
      el.dataset.wired = "1";
      if (el.parentElement && el.parentElement.id !== "toasts") {
        document.getElementById("toasts").appendChild(el);
      }
      hideLater(el);
    });
  }
  document.addEventListener("DOMContentLoaded", function () { wireToasts(); });
  document.addEventListener("htmx:afterSwap", function (e) { wireToasts(e.target); });

  // ------------------------------------------------------------ live sync
  // Every few seconds ask the server "did anything change?". If yes, fire an event
  // ("orders-changed", "menu-changed", "approvals-changed") that live parts of the page listen to.
  var last = {};
  var timer = null;
  var INTERVAL = 4000;
  function poll() {
    if (document.hidden) return;
    fetch("/live/pulse/", { credentials: "same-origin", headers: { "Accept": "application/json" } })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        if (!d) return;
        ["orders", "menu", "approvals_stamp"].forEach(function (k) {
          if (d[k] === undefined) return;
          if (last[k] !== undefined && last[k] !== d[k]) {
            var name = (k === "approvals_stamp" ? "approvals" : k) + "-changed";
            document.body.dispatchEvent(new CustomEvent(name, { bubbles: true }));
          }
          last[k] = d[k];
        });
        if (d.approvals !== undefined) {
          var c = document.getElementById("approval-count");
          if (c) { c.textContent = d.approvals; c.hidden = !d.approvals; }
        }
        setOnline(true);
      })
      .catch(function () { setOnline(false); });
  }
  var offlineToast = null;
  function setOnline(ok) {
    if (!ok && !offlineToast) {
      offlineToast = document.createElement("div");
      offlineToast.className = "toast toast-warn";
      offlineToast.textContent = "No internet — waiting to reconnect…";
      var box = document.getElementById("toasts");
      if (box) box.appendChild(offlineToast);
    } else if (ok && offlineToast) {
      offlineToast.remove(); offlineToast = null;
    }
  }
  function start() {
    if (timer || !document.body.hasAttribute("hx-headers")) return;
    poll();
    timer = setInterval(poll, INTERVAL);
  }
  document.addEventListener("visibilitychange", function () { if (!document.hidden) poll(); });
  document.addEventListener("DOMContentLoaded", start);

  // ------------------------------------------------------------ install as app
  if ("serviceWorker" in navigator) {
    window.addEventListener("load", function () { navigator.serviceWorker.register("/sw.js").catch(function () {}); });
  }

  // ------------------------------------------------------------ helpers
  window.inr = function (v) {
    v = Math.round((Number(v) || 0) * 100) / 100;
    var neg = v < 0; v = Math.abs(v);
    var s = v.toLocaleString("en-IN", { minimumFractionDigits: v % 1 ? 2 : 0, maximumFractionDigits: 2 });
    return (neg ? "-₹" : "₹") + s;
  };
  window.csrf = function () {
    var m = document.cookie.match(/csrftoken=([^;]+)/);
    return m ? decodeURIComponent(m[1]) : "";
  };

  // Confirm dangerous form submits: <form data-confirm="Sure?">
  document.addEventListener("submit", function (e) {
    var msg = e.target.getAttribute("data-confirm");
    if (msg && !window.confirm(msg)) e.preventDefault();
  });
})();
