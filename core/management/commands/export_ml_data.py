"""
Export clean, ML-ready tables (for the forecasting phase).

    python manage.py export_ml_data --out ml_data

Creates:
  daily_item_sales.csv   one row per business date × menu item size (zeros filled in)
  hourly_sales.csv       one row per business date × hour
  daily_stock.csv        one row per business date × stock item (used, bought, wasted, counted, variance)
  daily_totals.csv       one row per business date (sales, orders, cash, UPI, expenses, day of week)
"""

import csv
from collections import defaultdict
from datetime import timedelta
from pathlib import Path

from django.core.management.base import BaseCommand
from django.db.models import Count, Sum
from django.db.models.functions import ExtractHour
from django.utils import timezone

from money.models import Expense
from orders.models import Order, OrderItem, OrderStatus, Payment
from stock.models import StockCount, StockMovement


class Command(BaseCommand):
    help = "Export ML-ready CSVs (daily item sales, hourly sales, stock usage, daily totals)."

    def add_arguments(self, parser):
        parser.add_argument("--out", default="ml_data")

    def handle(self, *args, **opts):
        out = Path(opts["out"])
        out.mkdir(parents=True, exist_ok=True)
        live = Order.objects.exclude(status=OrderStatus.CANCELLED)
        dates = sorted(set(live.values_list("business_date", flat=True)))
        if not dates:
            self.stdout.write("No orders yet.")
            return
        all_days = [dates[0] + timedelta(days=i) for i in range((dates[-1] - dates[0]).days + 1)]

        # daily item sales (zero-filled grid, important for forecasting)
        sales = defaultdict(int)
        names = set()
        for r in (OrderItem.objects.filter(order__in=live)
                  .values("order__business_date", "item_name", "variant_name").annotate(q=Sum("quantity"))):
            key = (r["item_name"], r["variant_name"])
            names.add(key)
            sales[(r["order__business_date"], key)] = r["q"]
        open_days = set(dates)
        with open(out / "daily_item_sales.csv", "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["date", "weekday", "shop_open", "item", "size", "qty"])
            for d in all_days:
                for key in sorted(names):
                    w.writerow([d, d.isoweekday(), int(d in open_days), key[0], key[1], sales.get((d, key), 0)])

        tz = timezone.get_current_timezone()
        with open(out / "hourly_sales.csv", "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["date", "hour", "orders", "sales"])
            for r in (live.annotate(h=ExtractHour("created_at", tzinfo=tz)).values("business_date", "h")
                      .annotate(n=Count("id"), s=Sum("total")).order_by("business_date", "h")):
                w.writerow([r["business_date"], r["h"], r["n"], r["s"]])

        stock = defaultdict(lambda: defaultdict(float))
        for r in StockMovement.objects.values("business_date", "stock_item__name", "kind").annotate(s=Sum("delta")):
            stock[(r["business_date"], r["stock_item__name"])][r["kind"]] = float(r["s"] or 0)
        for c in StockCount.objects.select_related("stock_item"):
            row = stock[(c.business_date, c.stock_item.name)]
            row["counted"] = float(c.counted_qty)
            row["variance"] = float(c.variance)
        with open(out / "daily_stock.csv", "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["date", "stock_item", "used_in_sales", "purchased", "wasted", "staff_meal", "adjusted", "counted", "variance"])
            for (d, name), v in sorted(stock.items()):
                w.writerow([d, name, -v.get("sale", 0), v.get("purchase", 0), -v.get("wastage", 0),
                            -v.get("staff_meal", 0), v.get("adjust", 0), v.get("counted", ""), v.get("variance", "")])

        tot = {r["business_date"]: r for r in live.values("business_date").annotate(n=Count("id"), s=Sum("total"))}
        pay = defaultdict(dict)
        for r in Payment.objects.values("business_date", "method").annotate(s=Sum("amount")):
            pay[r["business_date"]][r["method"]] = float(r["s"] or 0)
        exp = {r["business_date"]: float(r["s"] or 0) for r in Expense.objects.values("business_date").annotate(s=Sum("amount"))}
        with open(out / "daily_totals.csv", "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["date", "weekday", "orders", "sales", "cash", "upi", "card", "online", "expenses"])
            for d in all_days:
                t = tot.get(d, {})
                p = pay.get(d, {})
                w.writerow([d, d.isoweekday(), t.get("n", 0), float(t.get("s") or 0), p.get("cash", 0), p.get("upi", 0),
                            p.get("card", 0), p.get("online", 0), exp.get(d, 0)])
        self.stdout.write(self.style.SUCCESS(f"Wrote 4 files to {out.resolve()}"))
