/* Billing screen (POS). Cart lives in the browser; the server re-checks every price. */
function posApp() {
  "use strict";
  var readJSON = function (id) { var el = document.getElementById(id); return el ? JSON.parse(el.textContent) : null; };
  var DRAFT_KEY = "ls-pos-draft";
  var post = function (url, body) {
    return fetch(url, {
      method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrf() },
      body: JSON.stringify(body || {}),
    }).then(function (r) {
      if (r.status === 403) throw new Error("Session expired — please log in again.");
      return r.json().catch(function () { throw new Error("Server error. Please try again."); });
    }).then(function (res) { if (!res.ok) throw new Error(res.error || "Could not save."); return res; });
  };

  return {
    menu: readJSON("menu-data") || { categories: [], items: [] },
    cfg: readJSON("pos-config") || {},
    edit: readJSON("edit-data"),
    view: "menu",            // "menu" | "orders"
    cat: null,
    q: "",
    lines: [],
    orderType: "dine_in",
    platform: "",
    tableNo: "",
    customerName: "",
    customerPhone: "",
    note: "",
    discount: "",
    discountNote: "",
    showExtras: false,
    cartOpen: false,
    picker: null,
    payOpen: false,
    payTarget: null,         // paying an existing bill without changing items
    method: "cash",
    tendered: "",
    reference: "",
    saving: false,
    done: null,
    error: "",
    autoPrint: false,
    recent: { open: [], done: [] },
    recentLoaded: false,
    busyId: null,

    init: function () {
      try { this.autoPrint = localStorage.getItem("ls-autoprint") === "1"; } catch (e) {}
      if (this.edit) this.loadOrder(this.edit);
      else this.restoreDraft();
      var self = this;
      this.$watch("lines", function () { self.saveDraft(); });
      ["orderType", "platform", "tableNo", "customerName", "discount", "note"].forEach(function (k) {
        self.$watch(k, function () { self.saveDraft(); });
      });
      // Online orders: Zomato is picked by default.
      this.$watch("orderType", function (t) { if (t === "online" && !self.platform) self.platform = "zomato"; });
      document.body.addEventListener("menu-changed", function () { self.reloadMenu(); });
      document.body.addEventListener("orders-changed", function () { self.loadRecent(); });
      this.loadRecent();
      document.addEventListener("keydown", function (e) {
        if (e.key === "/" && !/INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName)) {
          e.preventDefault(); self.view = "menu"; self.$refs.search && self.$refs.search.focus();
        }
        if (e.key === "Escape") { self.picker = null; if (!self.saving) { self.payOpen = false; self.payTarget = null; } }
      });
    },

    // ---------------------------------------------------------- menu
    get items() {
      var q = this.q.trim().toLowerCase();
      var cat = this.cat;
      return this.menu.items.filter(function (it) {
        if (q) return it.name.toLowerCase().indexOf(q) !== -1 || (it.label || "").toLowerCase().indexOf(q) !== -1;
        return cat === null || it.cat === cat;
      });
    },
    itemById: function (id) { return this.menu.items.find(function (i) { return i.id === id; }); },
    findVariant: function (vid) {
      for (var i = 0; i < this.menu.items.length; i++) {
        var it = this.menu.items[i];
        for (var j = 0; j < it.variants.length; j++) if (it.variants[j].id === vid) return { item: it, variant: it.variants[j] };
      }
      return null;
    },
    priceLabel: function (it) {
      var p = it.variants.map(function (v) { return v.price; });
      var lo = Math.min.apply(null, p), hi = Math.max.apply(null, p);
      return lo === hi ? inr(lo) : inr(lo) + "–" + inr(hi).replace("₹", "");
    },
    qtyOf: function (itemId) {
      return this.lines.reduce(function (s, l) { return s + (l.item_id === itemId ? l.qty : 0); }, 0);
    },
    reloadMenu: function () {
      var self = this;
      fetch(this.cfg.urls.menu, { credentials: "same-origin" }).then(function (r) { return r.json(); }).then(function (m) {
        self.menu = m;
        var changed = false;
        self.lines = self.lines.filter(function (l) {
          var f = self.findVariant(l.variant_id);
          if (!f || (f.item.soldOut && !l.min)) { changed = true; return false; }
          if (f.variant.price !== l.price) { l.price = f.variant.price; changed = true; }
          l.addons = l.addons.filter(function (a) { return f.item.addons.some(function (x) { return x.id === a.id; }); });
          return true;
        });
        if (changed) toast("Menu was updated — cart prices refreshed.", "warn");
      });
    },

    // ---------------------------------------------------------- open / recent orders
    loadRecent: function () {
      var self = this;
      fetch(this.cfg.urls.recent, { credentials: "same-origin" })
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (d) { if (d) { self.recent = d; self.recentLoaded = true; } })
        .catch(function () {});
    },
    get openCount() { return this.recent.open.length; },
    openOrder: function (id, thenPay) {
      // Load an existing bill into the cart so you can add items (e.g. a water bottle later).
      if (this.lines.length && !this.edit && !confirm("Your current cart will be cleared. Continue?")) return;
      var self = this;
      this.busyId = id;
      fetch("/orders/" + id + "/edit.json", { credentials: "same-origin" })
        .then(function (r) { return r.json(); })
        .then(function (res) {
          if (!res.ok) throw new Error(res.error || "Could not open the order.");
          self.done = null;
          self.loadOrder(res.order);
          self.view = "menu"; self.q = ""; self.cat = null;
          if (thenPay) self.openPay();
          else toast("Adding to " + res.order.label + " — tap items, then Save.", "info");
        })
        .catch(function (e) { toast(e.message, "bad"); })
        .finally(function () { self.busyId = null; });
    },
    payOrder: function (card) {
      this.payTarget = { id: card.id, label: card.label, due: card.balance };
      this.error = ""; this.tendered = ""; this.reference = "";
      this.method = "cash";
      this.payOpen = true;
    },
    setStatus: function (card, status) {
      var self = this;
      this.busyId = card.id;
      post("/orders/" + card.id + "/status.json", { status: status })
        .then(function () { self.loadRecent(); toast(card.label + " — " + (status === "completed" ? "done" : status), "ok"); })
        .catch(function (e) { toast(e.message, "bad"); })
        .finally(function () { self.busyId = null; });
    },

    // ---------------------------------------------------------- cart
    tap: function (it, ev) {
      if (it.soldOut) { toast(it.name + " is sold out", "bad"); return; }
      if (it.variants.length > 1) { this.picker = it; return; }
      this.add(it, it.variants[0]);
      if (ev && ev.currentTarget) {
        var el = ev.currentTarget; el.classList.remove("flash"); void el.offsetWidth; el.classList.add("flash");
      }
    },
    keyOf: function (vid, addons, note) {
      return vid + "|" + addons.map(function (a) { return a.id; }).sort().join(",") + "|" + (note || "");
    },
    add: function (it, v) {
      var key = this.keyOf(v.id, [], "");
      var line = this.lines.find(function (l) { return l.key === key; });
      if (line) { if (line.qty < 50) line.qty++; }
      else {
        this.lines.push({
          key: key, item_id: it.id, variant_id: v.id, name: it.name, size: it.variants.length > 1 ? v.name : "",
          price: v.price, addons: [], qty: 1, note: "", food: it.food, min: 0, isNew: !!this.edit,
        });
      }
      this.picker = null;
    },
    inc: function (l) { if (l.qty < 50) l.qty++; },
    dec: function (l) {
      if (l.min && l.qty <= l.min) { toast("Already paid/served — only the owner can remove items.", "warn"); return; }
      if (l.qty > 1) l.qty--;
      else this.lines.splice(this.lines.indexOf(l), 1);
    },
    remove: function (l) { this.lines.splice(this.lines.indexOf(l), 1); },
    allowedAddons: function (l) { var it = this.itemById(l.item_id); return it ? it.addons : []; },
    hasAddon: function (l, a) { return l.addons.some(function (x) { return x.id === a.id; }); },
    toggleAddon: function (l, a) {
      if (l.min) { toast("Add the extra as a new item instead (this one is already paid/served).", "warn"); return; }
      if (this.hasAddon(l, a)) l.addons = l.addons.filter(function (x) { return x.id !== a.id; });
      else l.addons = l.addons.concat([{ id: a.id, name: a.name, price: a.price }]);
      this.rekey(l);
    },
    rekey: function (l) {
      var key = this.keyOf(l.variant_id, l.addons, l.note);
      var other = this.lines.find(function (x) { return x !== l && x.key === key; });
      if (other) { other.qty = Math.min(50, other.qty + l.qty); this.remove(l); }
      else l.key = key;
    },
    lineTotal: function (l) {
      var a = l.addons.reduce(function (s, x) { return s + x.price; }, 0);
      return (l.price + a) * l.qty;
    },
    get count() { return this.lines.reduce(function (s, l) { return s + l.qty; }, 0); },
    get subtotal() { var self = this; return this.lines.reduce(function (s, l) { return s + self.lineTotal(l); }, 0); },
    get discountValue() {
      var d = parseFloat(this.discount);
      if (!isFinite(d) || d < 0) return 0;
      return Math.min(Math.round(d * 100) / 100, this.subtotal);
    },
    get total() { return Math.max(0, Math.round((this.subtotal - this.discountValue) * 100) / 100); },
    get alreadyPaid() { return this.edit ? (this.edit.paid || 0) : 0; },
    get due() { return Math.max(0, Math.round((this.total - this.alreadyPaid) * 100) / 100); },
    get payAmount() { return this.payTarget ? this.payTarget.due : this.due; },
    setDiscountPct: function (p) { this.discount = p ? String(Math.round(this.subtotal * p) / 100) : ""; },
    get capWarning() {
      if (this.cfg.staffDiscountCap === null || this.cfg.staffDiscountCap === undefined) return "";
      if (!this.discountValue) return "";
      if (this.edit && this.discountValue === (this.edit.discount_amount || 0)) return "";
      var cap = Math.round(this.subtotal * this.cfg.staffDiscountCap) / 100;
      return this.discountValue > cap ? "Above your limit (" + this.cfg.staffDiscountCap + "%). Save without discount and request approval." : "";
    },
    clear: function (force) {
      if (!force && this.lines.length && !confirm("Clear the whole cart?")) return;
      this.lines = []; this.discount = ""; this.discountNote = ""; this.customerName = ""; this.customerPhone = "";
      this.note = ""; this.platform = ""; this.tableNo = ""; this.orderType = "dine_in"; this.error = ""; this.showExtras = false;
      if (this.edit) { this.edit = null; if (location.search) history.replaceState(null, "", "/pos/"); }
      this.clearDraft();
    },
    loadOrder: function (o) {
      var self = this;
      var lockOld = !this.cfg.isOwner && (o.paid > 0 || o.status === "ready" || o.status === "completed");
      this.edit = o;
      this.orderType = o.order_type; this.platform = o.platform || ""; this.tableNo = o.table_no || "";
      this.customerName = o.customer_name || ""; this.customerPhone = o.customer_phone || ""; this.note = o.note || "";
      this.discount = o.discount_amount ? String(o.discount_amount) : ""; this.discountNote = o.discount_note || "";
      this.showExtras = false;
      this.lines = [];
      o.lines.forEach(function (raw) {
        var f = self.findVariant(raw.variant_id);
        if (!f) return;
        var addons = f.item.addons.filter(function (a) { return raw.addon_ids.indexOf(a.id) !== -1; })
          .map(function (a) { return { id: a.id, name: a.name, price: a.price }; });
        self.lines.push({
          key: self.keyOf(f.variant.id, addons, raw.note), item_id: f.item.id, variant_id: f.variant.id, name: f.item.name,
          size: f.item.variants.length > 1 ? f.variant.name : "", price: f.variant.price, addons: addons,
          qty: raw.qty, note: raw.note || "", food: f.item.food, min: lockOld ? raw.qty : 0, isNew: false,
        });
      });
    },

    // ---------------------------------------------------------- draft (survives a page reload)
    saveDraft: function () {
      if (this.edit) return;
      try {
        localStorage.setItem(DRAFT_KEY, JSON.stringify({
          lines: this.lines, orderType: this.orderType, platform: this.platform, tableNo: this.tableNo,
          customerName: this.customerName, discount: this.discount, note: this.note, at: Date.now(),
        }));
      } catch (e) {}
    },
    restoreDraft: function () {
      try {
        var d = JSON.parse(localStorage.getItem(DRAFT_KEY) || "null");
        if (!d || Date.now() - d.at > 6 * 3600 * 1000) return;
        var self = this;
        this.lines = (d.lines || []).filter(function (l) {
          var f = self.findVariant(l.variant_id);
          if (!f || f.item.soldOut) return false;
          l.price = f.variant.price; l.min = 0;
          return true;
        });
        this.orderType = d.orderType === "parcel" ? "takeaway" : (d.orderType || "dine_in");
        this.platform = d.platform || ""; this.tableNo = d.tableNo || "";
        this.customerName = d.customerName || ""; this.discount = d.discount || ""; this.note = d.note || "";
      } catch (e) {}
    },
    clearDraft: function () { try { localStorage.removeItem(DRAFT_KEY); } catch (e) {} },

    // ---------------------------------------------------------- the two big buttons
    // Dine-in: customers eat first and pay when leaving -> main button just places the order.
    // Takeaway: pay at the counter -> main button is Pay.
    // Online (Zomato / Swiggy / FoodAtDoor): already paid on the app -> one button, recorded as online money.
    get platformName() {
      var p = (this.cfg.platforms || []).find(function (x) { return x[0] === this.platform; }, this);
      return p ? p[1] : "online";
    },
    get mainAction() {
      if (this.orderType === "online") return { act: "save", label: (this.edit ? "Save" : "Place order") + " · paid on " + this.platformName };
      if (this.orderType === "takeaway" && (!this.edit || this.due > 0)) return { act: "pay", label: "Pay " + inr(this.due) };
      return { act: "save", label: this.edit ? "Save" : "Place order" };
    },
    get otherAction() {
      if (this.orderType === "online") return null;
      if (this.orderType === "takeaway") return (!this.edit || this.due > 0) ? { act: "save", label: this.edit ? "Save · pay later" : "Pay later" } : null;
      return this.due > 0 ? { act: "pay", label: "Pay " + inr(this.due) } : null;
    },
    run: function (a) { if (a.act === "pay") this.openPay(); else this.submit(false); },
    get qrUrl() {
      var note = this.payTarget ? this.payTarget.label : (this.edit ? this.edit.bill : "Lavish order");
      return "/upi/qr.svg?amount=" + this.payAmount.toFixed(2) + "&note=" + encodeURIComponent(note);
    },

    // ---------------------------------------------------------- pay & save
    openPay: function () {
      if (!this.lines.length) return;
      this.payTarget = null;
      this.error = ""; this.tendered = ""; this.reference = "";
      this.method = this.orderType === "online" ? "online" : "cash";
      this.payOpen = true;
    },
    get change() {
      var t = parseFloat(this.tendered);
      return this.method === "cash" && isFinite(t) && t >= this.payAmount ? Math.round((t - this.payAmount) * 100) / 100 : null;
    },
    get quickCash() {
      var t = this.payAmount, out = [];
      [50, 100, 200, 500, 2000].forEach(function (step) {
        var v = Math.ceil(t / step) * step;
        if (v > t && out.indexOf(v) === -1 && out.length < 4) out.push(v);
      });
      return out;
    },
    payload: function (withPayment) {
      var p = {
        order_type: this.orderType,
        platform: this.orderType === "online" ? this.platform : "",
        table_no: this.orderType === "dine_in" ? this.tableNo : "",
        customer_name: this.customerName,
        customer_phone: this.customerPhone,
        note: this.note,
        discount_amount: this.discountValue,
        discount_note: this.discountNote,
        lines: this.lines.map(function (l) {
          return { variant_id: l.variant_id, qty: l.qty, addon_ids: l.addons.map(function (a) { return a.id; }), note: l.note };
        }),
      };
      if (this.edit) p.version = this.edit.version;
      if (withPayment && this.due > 0) {
        p.payment = { method: this.method, amount: this.due, cash_tendered: this.method === "cash" ? this.tendered : "", reference: this.reference };
      } else if (this.orderType === "online" && this.due > 0) {
        p.payment = { method: "online", amount: this.due, reference: this.platformName };  // paid on the app
      }
      return p;
    },
    submit: function (withPayment) {
      if (this.saving) return;
      if (withPayment && this.method === "cash" && this.tendered !== "" && parseFloat(this.tendered) < this.payAmount) {
        this.error = "Cash given is less than the amount."; return;
      }
      if (this.payTarget) return this.submitPayOnly();
      if (!this.lines.length) return;
      this.saving = true; this.error = "";
      var self = this;
      var url = this.edit ? "/orders/" + this.edit.id + "/update/" : this.cfg.urls.create;
      var wasEdit = !!this.edit;
      post(url, this.payload(withPayment))
        .then(function (res) {
          res.change = withPayment ? self.change : null;
          res.edited = wasEdit;
          if (!withPayment) {
            // No money taken now: just confirm quietly so the next order can start straight away.
            toast((wasEdit ? "Updated " : "Order placed ") + res.label + (res.balance > 0 ? " · " + inr(res.balance) + " to pay later" : " · paid"), "ok");
          } else {
            self.done = res;
          }
          self.payOpen = false;
          self.cartOpen = false;
          self.clear(true);
          self.loadRecent();
          if (withPayment && self.autoPrint) self.print(res.id);
        })
        .catch(function (e) {
          self.error = e.message || "Network problem. Check internet and try again.";
          if (!self.payOpen) toast(self.error, "bad");
        })
        .finally(function () { self.saving = false; });
    },
    submitPayOnly: function () {
      var self = this, t = this.payTarget;
      this.saving = true; this.error = "";
      post("/orders/" + t.id + "/pay.json", {
        method: this.method, amount: t.due, cash_tendered: this.method === "cash" ? this.tendered : "", reference: this.reference,
      })
        .then(function (res) {
          res.change = self.change; res.edited = true;
          self.done = res; self.payOpen = false; self.payTarget = null;
          self.loadRecent();
          if (self.autoPrint) self.print(res.id);
        })
        .catch(function (e) { self.error = e.message; })
        .finally(function () { self.saving = false; });
    },
    collectFromDone: function () {
      var d = this.done;
      this.done = null;
      this.payOrder({ id: d.id, label: d.label, balance: d.balance });
    },
    print: function (id, width) {
      var url = "/orders/" + id + "/receipt/?print=1" + (width ? "&w=" + width : "");
      window.open(url, "_blank", "noopener");
    },
    toggleAutoPrint: function () {
      this.autoPrint = !this.autoPrint;
      try { localStorage.setItem("ls-autoprint", this.autoPrint ? "1" : "0"); } catch (e) {}
    },
    newOrder: function () {
      this.done = null;
      var s = this.$refs.search; if (s && window.innerWidth > 900) s.focus();
    },
  };
}
