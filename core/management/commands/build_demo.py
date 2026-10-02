"""
Build the demo sample database (demo_data/template.sqlite3).

    python manage.py build_demo

Runs automatically on every deploy (build.sh) and on the first "Try the demo" click if missing.
It never touches the real database: everything happens inside a separate SQLite file.
"""

import json
import os
import random
from datetime import datetime, timedelta
from decimal import Decimal as D
from unittest import mock

from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.db.models import Sum
from django.utils import timezone

from core import demo


def build(verbose=False):
    out = demo.template_path()
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(f"template.{os.getpid()}.building")
    if tmp.exists():
        tmp.unlink()
    with demo.use_sqlite(tmp):
        call_command("migrate", interactive=False, verbosity=0)
        call_command("seed_shop", verbosity=0)
        _fill()
    from django.contrib.contenttypes.models import ContentType

    ContentType.objects.clear_cache()
    os.replace(tmp, out)
    demo._meta_path().write_text(json.dumps({
        "built_on": timezone.localdate().isoformat(),
        "fingerprint": demo._migrations_fingerprint(),
    }))
    if verbose:
        print(f"Demo template ready: {out} ({out.stat().st_size // 1024} KB)")


def _fill():
    from accounts.models import User
    from core.models import ShopSettings
    from core.utils import business_date_for
    from menu.models import Addon, Variant
    from money.models import DayClose, Expense
    from money.services import day_summary
    from orders import services
    from orders.models import Order, OrderStatus, Payment
    from stock.models import RecipeLine, StockCount, StockItem, StockMovement
    from stock.services import day_stock

    rnd = random.Random(42)
    shop = ShopSettings.load()
    shop.tagline = "Demo — try anything!"
    shop.phone = "98765 43210"
    shop.staff_max_discount_percent = 5
    shop.upi_id = "demo.lavish@nopay"  # deliberately not a real UPI ID, so no money can be sent from the demo
    shop.save()

    owner = User.objects.create_superuser("demo", password=None, role=User.Role.ADMIN, display_name="Demo Owner")
    owner.set_unusable_password(); owner.save()
    staff = [
        User.objects.create_user("ravi", password=None, role=User.Role.STAFF, display_name="Ravi"),
        User.objects.create_user("priya", password=None, role=User.Role.STAFF, display_name="Priya"),
    ]
    for u in staff:
        u.set_unusable_password(); u.save()

    RecipeLine.objects.update(verified=True)
    costs = {"Chicken (raw)": "240", "Roti / Khubz": "8", "French fries (frozen)": "180", "Water bottle": "6"}
    for it in StockItem.objects.all():
        it.cost_per_unit = D(costs.get(it.name, "9" if "Small" in it.name else "12" if "Medium" in it.name else "15"))
        it.save()
    stock = {s.name: s for s in StockItem.objects.all()}

    variants = list(Variant.objects.select_related("item"))
    weights = [12 if "Shawarma" in v.item.name else 5 if v.item.category.name != "Drinks" else 4 for v in variants]
    mayo = Addon.objects.first()
    tz = timezone.get_current_timezone()
    today = timezone.localdate()

    # Opening stock, counted the evening before the history starts
    first = today - timedelta(days=10)
    for name, qty in {"Chicken (raw)": "6", "Roti / Khubz": "50", "French fries (frozen)": "3"}.items():
        StockCount.objects.create(business_date=first - timedelta(days=1), stock_item=stock[name], counted_qty=D(qty),
                                  expected_qty=D(qty), counted_by=owner)
    for s in stock.values():
        if s.name.startswith(("Thumbs", "Sprite", "Water")):
            StockCount.objects.create(business_date=first - timedelta(days=1), stock_item=s, counted_qty=D("24"),
                                      expected_qty=D("24"), counted_by=owner)

    for back in range(10, 0, -1):
        day = today - timedelta(days=back)
        weekend = day.weekday() >= 4
        n = rnd.randint(26, 38) + (14 if weekend else 0)

        def at(h, m):
            return datetime(day.year, day.month, day.day, h, m, tzinfo=tz)

        # morning purchases (also saved as expenses)
        for name, qty, price in (("Chicken (raw)", D(9 if weekend else 7), D("240")), ("Roti / Khubz", D(110 if weekend else 80), D("8")),
                                 ("French fries (frozen)", D(3), D("180"))):
            with mock.patch("django.utils.timezone.now", return_value=at(10, 5)):
                mv = StockMovement.objects.create(stock_item=stock[name], kind="purchase", delta=qty, unit_cost=price,
                                                  business_date=day, created_by=owner, created_at=at(10, 5))
                Expense.objects.create(business_date=day, category="stock", description=f"{name} {qty}",
                                       amount=qty * price, paid_from="upi", stock_movement=mv, created_by=owner, created_at=at(10, 5))
        if back % 3 == 0:
            for name in [s for s in stock if s.startswith(("Thumbs", "Sprite", "Water"))]:
                StockMovement.objects.create(stock_item=stock[name], kind="purchase", delta=D("24"), unit_cost=stock[name].cost_per_unit,
                                             business_date=day, created_by=owner)
        Expense.objects.create(business_date=day, category="gas", description="Gas refill", amount=D("180"), created_by=owner)
        if back == 5:
            Expense.objects.create(business_date=day, category="salary", description="Weekly wages", amount=D("3500"),
                                   paid_from="upi", created_by=owner)

        for _ in range(n):
            hour = rnd.choices([12, 13, 14, 16, 17, 18, 19, 20, 21, 22], [2, 3, 2, 1, 2, 4, 6, 7, 6, 3])[0]
            when = at(hour, rnd.randint(0, 59))
            lines = []
            for v in rnd.choices(variants, weights, k=rnd.randint(1, 3)):
                addons = [mayo.id] if rnd.random() < 0.2 and v.item.addons.filter(pk=mayo.pk).exists() else []
                lines.append({"variant_id": v.id, "qty": rnd.choice([1, 1, 1, 2]), "addon_ids": addons})
            otype = rnd.choices(["dine_in", "takeaway", "online"], [5, 4, 3])[0]
            payload = {
                "order_type": otype,
                "table_no": str(rnd.randint(1, 8)) if otype == "dine_in" else "",
                "platform": rnd.choice(["zomato", "zomato", "swiggy", "foodatdoor"]) if otype == "online" else "",
                "lines": lines,
                "payment": {"method": "online" if otype == "online" else rnd.choices(["cash", "upi", "card"], [4, 6, 1])[0], "amount": None},
            }
            user = rnd.choice([owner] + staff)
            with mock.patch("django.utils.timezone.now", return_value=when):
                o = services.create_order(owner, None, payload)
            Order.objects.filter(pk=o.pk).update(status=OrderStatus.COMPLETED, created_by=user, completed_at=when + timedelta(minutes=12))
            Payment.objects.filter(order=o).update(created_at=when + timedelta(minutes=1), received_by=user)
            if rnd.random() < 0.02:
                with mock.patch("django.utils.timezone.now", return_value=when + timedelta(minutes=3)):
                    services.cancel_order(o, owner, None, "Customer changed mind")

        # wastage now and then
        if back % 4 == 0:
            StockMovement.objects.create(stock_item=stock["Roti / Khubz"], kind="wastage", delta=D("-3"), unit_cost=D("8"),
                                         business_date=day, note="torn rotis", created_by=owner)
        # evening count: small realistic differences so the "missing stock" report has something to show
        counted_loss = D("0")
        for r in day_stock(day):
            if r.item.name in ("Chicken (raw)", "Roti / Khubz") or r.item.name.startswith(("Thumbs", "Sprite", "Water")):
                miss = {"Chicken (raw)": D(rnd.choice(["0", "0.15", "0.25", "0.4"])), "Roti / Khubz": D(rnd.choice([0, 0, 2, 4]))}.get(r.item.name, D(rnd.choice([0, 0, 0, 1])))
                counted = max(D("0"), r.expected - miss)
                var = counted - r.expected
                val = (var * r.item.cost_per_unit).quantize(D("0.01"))
                StockCount.objects.create(business_date=day, stock_item=r.item, counted_qty=counted, expected_qty=r.expected,
                                          variance=var, variance_value=val, counted_by=owner)
                if val < 0:
                    counted_loss += val
        s = day_summary(day)
        counted_cash = max(D("0"), s.expected_cash - D(rnd.choice([0, 0, 0, 10, 20, 50])))
        DayClose.objects.create(
            business_date=day, opening_cash=s.opening_cash, cash_sales=s.by_method["cash"], cash_expenses=s.expenses_from_drawer,
            expected_cash=s.expected_cash, counted_cash=counted_cash, cash_difference=counted_cash - s.expected_cash,
            cash_removed=max(D("0"), counted_cash - D("1500")), upi_sales=s.by_method["upi"], card_sales=s.by_method["card"],
            online_sales=s.by_method["online"], total_sales=s.sales, unpaid_amount=s.unpaid, orders_count=s.orders_count,
            cancelled_count=s.cancelled_count, total_expenses=s.expenses, stock_loss_value=-counted_loss, closed_by=owner,
        )


class Command(BaseCommand):
    help = "Build the demo sample database used by 'Try the demo' (never touches the real database)."

    def handle(self, *args, **opts):
        build(verbose=True)
