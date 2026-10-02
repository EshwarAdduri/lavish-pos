"""
Load Lavish Shawarma's starting menu and stock items.

Safe to run many times: it only adds things when the menu is empty,
so it never undoes changes the owner made in the app.
Use --force to add any missing starter items even if a menu already exists.
"""

from decimal import Decimal as D

from django.core.management.base import BaseCommand
from django.db import transaction

from core.models import ShopSettings
from menu.models import Addon, Category, FoodType, Item, Variant
from stock.models import RecipeLine, StockItem

NV, V = FoodType.NON_VEG, FoodType.VEG

# (category, item, food type, [(size, price)], {stock item: qty per unit})
MENU = [
    ("Shawarma", "Chicken Regular Shawarma (with salad)", NV, [("Regular", 110)], {"Chicken (raw)": "0.120", "Roti / Khubz": 1}),
    ("Shawarma", "Chicken Special Shawarma (without salad)", NV, [("Regular", 130)], {"Chicken (raw)": "0.120", "Roti / Khubz": 1}),
    ("Shawarma", "Chicken Chips Shawarma", NV, [("Regular", 140)], {"Chicken (raw)": "0.120", "Roti / Khubz": 1, "French fries (frozen)": "0.040"}),
    ("Shawarma", "Chicken Peri-Peri Shawarma", NV, [("Regular", 140)], {"Chicken (raw)": "0.120", "Roti / Khubz": 1}),
    ("Shawarma", "Chicken Schezwan Shawarma", NV, [("Regular", 150)], {"Chicken (raw)": "0.120", "Roti / Khubz": 1}),
    ("Shawarma", "Chicken Cheese Shawarma", NV, [("Regular", 150)], {"Chicken (raw)": "0.120", "Roti / Khubz": 1}),
    ("Shawarma", "Chicken Turkish Shawarma", NV, [("Regular", 150)], {"Chicken (raw)": "0.120", "Roti / Khubz": 1}),
    ("Shawarma", "Lavish Special Shawarma", NV, [("Regular", 200)], {"Chicken (raw)": "0.180", "Roti / Khubz": 1}),
    ("Shawarma", "Special Veg Falafel Shawarma", V, [("Regular", 110)], {"Roti / Khubz": 1}),
    ("Bowls", "Chicken Loaded Bowl", NV, [("Regular", 200)], {"Chicken (raw)": "0.180"}),
    ("Fries", "French Fries", V, [("Regular", 90)], {"French fries (frozen)": "0.150"}),
    ("Fries", "French Fries with Peri-Peri", V, [("Regular", 90)], {"French fries (frozen)": "0.150"}),
    ("Drinks", "Water bottle", V, [("Regular", 10)], {"Water bottle": 1}),
    ("Drinks", "Thumbs Up", V, [("Small", 10), ("Medium", 15), ("Large", 20)], "Thumbs Up"),
    ("Drinks", "Sprite", V, [("Small", 10), ("Medium", 15), ("Large", 20)], "Sprite"),
]

CATEGORIES = ["Shawarma", "Bowls", "Fries", "Drinks"]

# name, unit, low-stock level, key item?
STOCK = [
    ("Chicken (raw)", "kg", "3", True),
    ("Roti / Khubz", "pcs", "30", True),
    ("French fries (frozen)", "kg", "1", False),
    ("Water bottle", "pcs", "6", False),
    ("Thumbs Up Small", "pcs", "6", False),
    ("Thumbs Up Medium", "pcs", "6", False),
    ("Thumbs Up Large", "pcs", "6", False),
    ("Sprite Small", "pcs", "6", False),
    ("Sprite Medium", "pcs", "6", False),
    ("Sprite Large", "pcs", "6", False),
]


class Command(BaseCommand):
    help = "Load the starting Lavish Shawarma menu and stock items (only if the menu is empty)."

    def add_arguments(self, parser):
        parser.add_argument("--force", action="store_true", help="Add missing starter items even if a menu exists.")

    @transaction.atomic
    def handle(self, *args, **opts):
        ShopSettings.load()
        if Category.objects.exists() and not opts["force"]:
            self.stdout.write("Menu already exists — nothing to do.")
            return

        stock = {}
        for i, (name, unit, low, key) in enumerate(STOCK):
            stock[name], _ = StockItem.objects.get_or_create(
                name=name, defaults={"unit": unit, "low_stock_level": D(low), "is_key": key, "sort_order": i * 10}
            )

        cats = {}
        for i, name in enumerate(CATEGORIES):
            cats[name], _ = Category.objects.get_or_create(name=name, defaults={"sort_order": i * 10})

        mayo, _ = Addon.objects.get_or_create(name="Extra Mayonnaise", defaults={"price": D("10"), "food_type": NV})

        for i, (cat, name, ftype, sizes, recipe) in enumerate(MENU):
            item, _ = Item.objects.get_or_create(
                category=cats[cat], name=name, defaults={"food_type": ftype, "sort_order": i * 10}
            )
            if cat in ("Shawarma", "Bowls", "Fries"):
                item.addons.add(mayo)
            for j, (size, price) in enumerate(sizes):
                v, _ = Variant.objects.get_or_create(item=item, name=size, defaults={"price": D(price), "sort_order": j * 10})
                if isinstance(recipe, str):  # drinks: one bottle of the matching size
                    lines = {f"{recipe} {size}": 1}
                    verified = True
                else:
                    lines = recipe
                    verified = False  # owner must confirm grams per item
                for stock_name, qty in lines.items():
                    RecipeLine.objects.get_or_create(
                        stock_item=stock[stock_name], variant=v,
                        defaults={"quantity": D(str(qty)), "verified": verified},
                    )
        self.stdout.write(self.style.SUCCESS(
            f"Loaded {Item.objects.count()} items, {Variant.objects.count()} sizes, {StockItem.objects.count()} stock items."
        ))
