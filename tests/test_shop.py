"""
Automated checks for the money and stock rules.   Run with:   python manage.py test
"""

import json
from datetime import timedelta
from decimal import Decimal as D

from django.core.management import call_command
from django.test import Client, TestCase

from accounts.models import User
from core.models import AuditLog, ShopSettings
from core.utils import business_date_for, inr, today
from menu.models import Addon, Variant
from money.models import DayClose, Expense
from money.services import day_summary
from orders import services
from orders.models import ApprovalRequest, Order, OrderStatus, Payment
from stock.models import StockCount, StockItem, StockMovement
from stock.services import day_stock


class Base(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_shop", verbosity=0)
        cls.owner = User.objects.create_user("owner", password="Owner@12345", role=User.Role.ADMIN)
        cls.staff = User.objects.create_user("ravi", password="Staff@12345", role=User.Role.STAFF)
        cls.peri = Variant.objects.get(item__name="Chicken Peri-Peri Shawarma")      # ₹140
        cls.thumbs_l = Variant.objects.get(item__name="Thumbs Up", name="Large")      # ₹20
        cls.mayo = Addon.objects.get(name="Extra Mayonnaise")                         # ₹10
        cls.chicken = StockItem.objects.get(name="Chicken (raw)")
        cls.roti = StockItem.objects.get(name="Roti / Khubz")

    def order(self, user=None, lines=None, **kw):
        payload = {"order_type": "takeaway", "lines": lines or [{"variant_id": self.peri.id, "qty": 1}], **kw}
        return services.create_order(user or self.owner, None, payload)


class OrderMathTests(Base):
    def test_totals_come_from_server_prices(self):
        o = self.order(lines=[
            {"variant_id": self.peri.id, "qty": 2, "addon_ids": [self.mayo.id], "price": 1},  # price from browser ignored
            {"variant_id": self.thumbs_l.id, "qty": 1},
        ])
        self.assertEqual(o.subtotal, D("320.00"))   # 2×(140+10) + 20
        self.assertEqual(o.total, D("320.00"))
        self.assertEqual(o.items.count(), 2)

    def test_discount_and_cash_payment(self):
        o = self.order(discount_amount="20", payment={"method": "cash", "amount": "120", "cash_tendered": "200"})
        self.assertEqual(o.total, D("120.00"))
        self.assertTrue(o.is_paid)
        p = o.payments.get()
        self.assertEqual(p.change, D("80.00"))

    def test_cannot_overpay_or_underpay_cash(self):
        o = self.order()
        with self.assertRaises(services.OrderError):
            services.add_payment(o, self.owner, None, "upi", "500")
        with self.assertRaises(services.OrderError):
            services.add_payment(o, self.owner, None, "cash", "140", cash_tendered="100")

    def test_bill_and_token_numbers_increase(self):
        a, b = self.order(), self.order()
        self.assertEqual(b.bill_no, a.bill_no + 1)
        self.assertEqual(b.token_no, a.token_no + 1)
        self.assertTrue(a.bill_number.startswith("LS-"))

    def test_price_change_does_not_change_old_bills(self):
        o = self.order()
        self.peri.price = D("160"); self.peri.save()
        o.refresh_from_db()
        self.assertEqual(o.total, D("140.00"))
        self.assertEqual(o.items.get().unit_price, D("140.00"))

    def test_sold_out_and_bad_input_rejected(self):
        self.peri.item.is_sold_out = True; self.peri.item.save()
        with self.assertRaises(services.OrderError):
            self.order()
        with self.assertRaises(services.OrderError):
            self.order(lines=[{"variant_id": self.thumbs_l.id, "qty": 0}])
        with self.assertRaises(services.OrderError):  # mayo isn't allowed on drinks
            self.order(lines=[{"variant_id": self.thumbs_l.id, "qty": 1, "addon_ids": [self.mayo.id]}])


class StaffLimitTests(Base):
    def test_staff_discount_over_limit_blocked(self):
        with self.assertRaises(services.OrderError):
            self.order(user=self.staff, discount_amount="10")
        self.assertEqual(Order.objects.count(), 0)  # nothing half-saved

    def test_max_open_orders(self):
        shop = ShopSettings.load(); shop.max_open_orders_per_staff = 2; shop.max_orders_per_minute = 50; shop.save()
        self.order(user=self.staff); self.order(user=self.staff)
        with self.assertRaises(services.OrderError):
            self.order(user=self.staff)
        self.order(user=self.owner)  # owner is never limited

    def test_rate_limit(self):
        shop = ShopSettings.load(); shop.max_orders_per_minute = 2; shop.max_open_orders_per_staff = 50; shop.save()
        self.order(user=self.staff); self.order(user=self.staff)
        with self.assertRaises(services.OrderError):
            self.order(user=self.staff)

    def test_staff_cannot_cancel_directly_but_can_request(self):
        o = self.order(user=self.staff, payment={"method": "cash", "amount": "140"})
        with self.assertRaises(services.OrderError):
            services.cancel_order(o, self.staff, None, "mistake")
        req = services.request_approval(o, self.staff, "cancel", "customer left")
        services.decide(req, self.owner, None, approve=True)
        o.refresh_from_db()
        self.assertEqual(o.status, OrderStatus.CANCELLED)
        self.assertEqual(o.paid_amount, D("0"))  # refunded
        self.assertEqual(Payment.objects.filter(order=o).count(), 2)

    def test_discount_approval_applies(self):
        o = self.order(user=self.staff)
        req = services.request_approval(o, self.staff, "discount", "regular customer", "15")
        services.decide(req, self.owner, None, approve=True)
        o.refresh_from_db()
        self.assertEqual(o.total, D("125.00"))
        self.assertEqual(req.order.approvals.get().status, ApprovalRequest.Status.APPROVED)

    def test_staff_can_add_to_paid_order_but_not_remove(self):
        o = self.order(user=self.staff, lines=[{"variant_id": self.peri.id, "qty": 2}], payment={"method": "upi", "amount": "280"})
        services.set_status(o, self.staff, "completed")
        o.refresh_from_db()
        # 5 minutes later the table orders a water bottle: allowed, bill goes back to the kitchen, ₹10 due
        water = Variant.objects.get(item__name="Water bottle")
        o = services.update_order(o, self.staff, None, {"lines": [{"variant_id": self.peri.id, "qty": 2},
                                                                   {"variant_id": water.id, "qty": 1}], "version": o.version})
        self.assertEqual(o.total, D("290.00"))
        self.assertEqual(o.balance, D("10.00"))
        self.assertEqual(o.status, OrderStatus.PENDING)
        # removing a paid shawarma is owner-only
        with self.assertRaises(services.OrderError):
            services.update_order(o, self.staff, None, {"lines": [{"variant_id": self.peri.id, "qty": 1},
                                                                   {"variant_id": water.id, "qty": 1}], "version": o.version})
        o = services.update_order(o, self.owner, None, {"lines": [{"variant_id": self.peri.id, "qty": 1},
                                                                   {"variant_id": water.id, "qty": 1}], "version": o.version})
        self.assertEqual(o.total, D("150.00"))

    def test_table_number_only_for_dine_in(self):
        a = self.order(order_type="dine_in", table_no=" 4 ")
        b = self.order(order_type="takeaway", table_no="4")
        self.assertEqual(a.table_no, "4")
        self.assertEqual(a.label, f"#{a.token_no} · T4")
        self.assertEqual(b.table_no, "")

    def test_old_version_rejected(self):
        o = self.order()
        services.update_order(o, self.owner, None, {"lines": [{"variant_id": self.peri.id, "qty": 2}], "version": o.version})
        with self.assertRaises(services.OrderError):  # second phone still had the old copy open
            services.update_order(o, self.owner, None, {"lines": [{"variant_id": self.peri.id, "qty": 3}], "version": o.version})

    def test_pages_staff_cannot_open(self):
        c = Client()
        c.login(username="ravi", password="Staff@12345")
        for url in ["/money/", "/menu/", "/team/", "/settings/", "/audit/", "/money/reports/", "/stock/recipes/"]:
            self.assertEqual(c.get(url).status_code, 403, url)
        self.assertEqual(c.get("/pos/").status_code, 200)


class V3Tests(Base):
    def test_default_type_is_dine_in_and_online_defaults_to_zomato(self):
        self.assertEqual(self.order(order_type="").order_type, "dine_in")
        o = self.order(order_type="online")
        self.assertEqual(o.platform, "zomato")
        self.assertEqual(self.order(order_type="online", platform="foodatdoor").get_platform_display(), "FoodAtDoor")

    def test_dine_in_closes_when_table_pays_on_leaving(self):
        o = self.order(order_type="dine_in", table_no="2")
        self.assertEqual(o.status, OrderStatus.PENDING)
        services.add_payment(o, self.owner, None, "cash", "140")
        o.refresh_from_db()
        self.assertEqual(o.status, OrderStatus.COMPLETED)

    def test_paying_up_front_keeps_order_in_kitchen(self):
        o = self.order(order_type="takeaway", payment={"method": "upi", "amount": "140"})
        self.assertEqual(o.status, OrderStatus.PENDING)
        o2 = self.order(order_type="dine_in", payment={"method": "cash", "amount": "140"})
        self.assertEqual(o2.status, OrderStatus.PENDING)

    def test_completed_is_final(self):
        o = self.order(payment={"method": "cash", "amount": "140"})
        services.set_status(o, self.owner, "completed")
        with self.assertRaises(services.OrderError):
            services.set_status(o, self.owner, "ready")
        c = Client(); c.login(username="owner", password="Owner@12345")
        self.assertNotContains(c.get(f"/orders/{o.pk}/"), "Mark ready")

    def test_upi_qr(self):
        from core.models import ShopSettings
        c = Client(); c.login(username="owner", password="Owner@12345")
        self.assertEqual(c.get("/upi/qr.svg?amount=150").status_code, 400)  # no UPI ID yet
        shop = ShopSettings.load(); shop.upi_id = "lavish@okaxis"; shop.save()
        r = c.get("/upi/qr.svg?amount=150&note=LS-1")
        self.assertEqual(r.status_code, 200)
        self.assertIn("svg", r["Content-Type"])
        o = self.order()
        self.assertContains(c.get(f"/orders/{o.pk}/receipt/"), "Scan to pay")
        self.assertEqual(c.get(f"/orders/{o.pk}/receipt.pdf").status_code, 200)
        from core.upi import upi_link
        self.assertEqual(upi_link(shop, D("150"), "LS-1"), "upi://pay?pa=lavish@okaxis&pn=Lavish%20Shawarma&cu=INR&am=150.00&tn=LS-1")


class StockTests(Base):
    def test_sale_uses_recipe_and_cancel_returns_it(self):
        o = self.order(lines=[{"variant_id": self.peri.id, "qty": 3}])
        used = {m.stock_item_id: m.delta for m in StockMovement.objects.filter(order=o)}
        self.assertEqual(used[self.chicken.id], D("-0.360"))   # 3 × 0.120 kg
        self.assertEqual(used[self.roti.id], D("-3"))
        services.cancel_order(o, self.owner, None, "test")
        self.assertFalse(StockMovement.objects.filter(order=o).exists())

    def test_edit_replaces_usage(self):
        o = self.order(lines=[{"variant_id": self.peri.id, "qty": 1}])
        services.update_order(o, self.owner, None, {"lines": [{"variant_id": self.peri.id, "qty": 4}], "version": o.version})
        self.assertEqual(StockMovement.objects.get(order=o, stock_item=self.roti).delta, D("-4"))

    def test_missing_stock_calculation(self):
        d = today()
        StockMovement.objects.create(stock_item=self.roti, kind="purchase", delta=D("50"), unit_cost=D("8"), business_date=d)
        StockMovement.objects.create(stock_item=self.roti, kind="wastage", delta=D("-2"), unit_cost=D("8"), business_date=d)
        self.roti.cost_per_unit = D("8"); self.roti.save()
        self.order(lines=[{"variant_id": self.peri.id, "qty": 10}])
        row = next(r for r in day_stock(d) if r.item == self.roti)
        self.assertEqual(row.expected, D("38"))           # 50 − 2 − 10
        StockCount.objects.create(business_date=d, stock_item=self.roti, counted_qty=D("35"))
        row = next(r for r in day_stock(d) if r.item == self.roti)
        self.assertEqual(row.variance, D("-3"))            # 3 rotis missing
        self.assertEqual(row.variance_value, D("-24.00"))  # worth ₹24
        # next day opens from the counted number, not the expected one
        nxt = next(r for r in day_stock(d + timedelta(days=1)) if r.item == self.roti)
        self.assertEqual(nxt.opening, D("35"))


class MoneyTests(Base):
    def test_day_summary_and_cash_drawer(self):
        d = today()
        self.order(payment={"method": "cash", "amount": "140"})
        self.order(payment={"method": "upi", "amount": "140"})
        self.order()  # unpaid
        Expense.objects.create(business_date=d, description="gas", amount=D("50"), paid_from="drawer", created_by=self.owner)
        Expense.objects.create(business_date=d, description="rent", amount=D("1000"), paid_from="upi", created_by=self.owner)
        s = day_summary(d)
        self.assertEqual(s.sales, D("420.00"))
        self.assertEqual(s.by_method["cash"], D("140.00"))
        self.assertEqual(s.by_method["upi"], D("140.00"))
        self.assertEqual(s.unpaid, D("140.00"))
        self.assertEqual(s.expected_cash, D("90.00"))  # 0 opening + 140 cash − 50 from drawer

    def test_day_close_page(self):
        self.order(payment={"method": "cash", "amount": "140"})
        c = Client(); c.login(username="owner", password="Owner@12345")
        r = c.post("/money/close/", {"date": today().isoformat(), "opening_cash": "500", "counted_cash": "630",
                                     "cash_removed": "400", f"count-{self.roti.id}": "0"})
        self.assertEqual(r.status_code, 302)
        dc = DayClose.objects.get(business_date=today())
        self.assertEqual(dc.expected_cash, D("640.00"))
        self.assertEqual(dc.cash_difference, D("-10.00"))   # ₹10 short
        self.assertEqual(dc.closing_balance, D("230.00"))   # tomorrow's opening

    def test_exports_and_dashboard_render(self):
        o = self.order(payment={"method": "cash", "amount": "140"})
        c = Client(); c.login(username="owner", password="Owner@12345")
        for url in ["/money/", "/money/reports/?range=30d", "/money/export/report.xlsx", "/money/export/orders.csv",
                    "/stock/", "/stock/prep/", f"/orders/{o.pk}/receipt.pdf", f"/orders/{o.pk}/", "/orders/board/"]:
            self.assertEqual(c.get(url).status_code, 200, url)


class MiscTests(Base):
    def test_indian_money_format(self):
        self.assertEqual(inr(D("123456.5")), "₹1,23,456.50")
        self.assertEqual(inr(140), "₹140")
        self.assertEqual(inr(-20), "-₹20")

    def test_late_night_counts_as_previous_day(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        late = datetime(2026, 10, 3, 1, 30, tzinfo=ZoneInfo("Asia/Kolkata"))
        self.assertEqual(business_date_for(late, 4).isoformat(), "2026-10-02")

    def test_audit_log_records_price_change(self):
        self.peri.price = D("150"); self.peri.save()
        log = AuditLog.objects.filter(model="Variant", action="update").latest("id")
        self.assertEqual(log.changes["price"], ["140.00", "150.00"])

    def test_login_lockout(self):
        c = Client()
        for _ in range(5):
            c.post("/login/", {"username": "owner", "password": "wrong"})
        r = c.post("/login/", {"username": "owner", "password": "Owner@12345"})
        self.assertContains(r, "Too many wrong attempts")

    def test_create_order_api(self):
        c = Client(); c.login(username="ravi", password="Staff@12345")
        r = c.post("/orders/new/", json.dumps({"lines": [{"variant_id": self.peri.id, "qty": 1}],
                                               "payment": {"method": "cash", "amount": 140}}), content_type="application/json")
        self.assertEqual(r.json()["total"], 140.0)
        self.assertEqual(r.json()["balance"], 0.0)


import shutil
import tempfile

from django.test import override_settings

_DEMO_TMP = tempfile.mkdtemp(prefix="lavish-demo-test-")


@override_settings(DEMO_DIR=_DEMO_TMP, DEMO_PER_IP_PER_HOUR=100)
class DemoTests(Base):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(_DEMO_TMP, ignore_errors=True)

    def test_demo_is_isolated_from_real_data(self):
        real_orders, real_users = Order.objects.count(), User.objects.count()
        c = Client()
        r = c.post("/demo/start/")
        self.assertEqual(r.status_code, 302)
        r = c.get("/demo/enter/", follow=True)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "resets in")
        # make orders inside the demo
        v = self.peri.id
        for _ in range(3):
            r = c.post("/orders/new/", json.dumps({"lines": [{"variant_id": v, "qty": 1}]}), content_type="application/json")
            self.assertTrue(r.json()["ok"], r.content)
        self.assertEqual(c.get("/money/").status_code, 200)
        self.assertGreater(c.get("/pos/recent.json").json()["open"].__len__(), 0)
        # the real database did not change
        self.assertEqual(Order.objects.count(), real_orders)
        self.assertEqual(User.objects.count(), real_users)
        self.assertFalse(User.objects.filter(username="demo").exists())
        # ending the demo deletes its file
        from core import demo
        self.assertEqual(len(list(demo.sandbox_dir().glob("*.sqlite3"))), 1)
        c.get("/demo/end/")
        self.assertEqual(len(list(demo.sandbox_dir().glob("*.sqlite3"))), 0)

    def test_demo_expires(self):
        from core import demo
        c = Client()
        c.post("/demo/start/")
        c.get("/demo/enter/")
        with override_settings(DEMO_MINUTES=0):
            r = c.get("/pos/")
        self.assertRedirects(r, "/login/?demo=ended", fetch_redirect_response=False)
        demo.cleanup(force=True)
