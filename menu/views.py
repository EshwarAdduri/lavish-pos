from django import forms
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Prefetch, ProtectedError
from django.forms import inlineformset_factory
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from core.models import ShopSettings
from core.permissions import owner_required

from .models import Addon, Category, Item, Variant


@owner_required
def menu_home(request):
    cats = Category.objects.prefetch_related(
        Prefetch("items", queryset=Item.objects.prefetch_related("variants", "addons").order_by("sort_order", "name"))
    )
    return render(request, "menu/home.html", {"categories": cats, "addons": Addon.objects.all()})


# ---------------------------------------------------------------- categories
class CategoryForm(forms.ModelForm):
    class Meta:
        model = Category
        fields = ["name", "sort_order", "is_active"]


@owner_required
def category_edit(request, pk=None):
    obj = get_object_or_404(Category, pk=pk) if pk else None
    form = CategoryForm(request.POST or None, instance=obj)
    if request.method == "POST":
        if "delete" in request.POST and obj:
            try:
                obj.delete()
                messages.success(request, "Category deleted.")
            except ProtectedError:
                messages.error(request, "This category still has items. Move or delete them first, or just hide it.")
            return redirect("menu")
        if form.is_valid():
            form.save()
            messages.success(request, "Category saved.")
            return redirect("menu")
    return render(request, "menu/simple_form.html", {"form": form, "obj": obj, "title": "Category", "can_delete": bool(obj)})


# ---------------------------------------------------------------- add-ons
class AddonForm(forms.ModelForm):
    class Meta:
        model = Addon
        fields = ["name", "price", "food_type", "sort_order", "is_active"]


@owner_required
def addon_edit(request, pk=None):
    obj = get_object_or_404(Addon, pk=pk) if pk else None
    form = AddonForm(request.POST or None, instance=obj)
    if request.method == "POST":
        if "delete" in request.POST and obj:
            obj.delete()
            messages.success(request, "Extra deleted.")
            return redirect("menu")
        if form.is_valid():
            form.save()
            messages.success(request, "Extra saved.")
            return redirect("menu")
    return render(request, "menu/simple_form.html", {"form": form, "obj": obj, "title": "Extra / add-on", "can_delete": bool(obj)})


# ---------------------------------------------------------------- items
class ItemForm(forms.ModelForm):
    class Meta:
        model = Item
        fields = ["category", "name", "short_name", "food_type", "sort_order", "is_active", "is_sold_out", "addons"]
        widgets = {"addons": forms.CheckboxSelectMultiple}


VariantFormSet = inlineformset_factory(
    Item, Variant, fields=["name", "price", "sort_order", "is_active"], extra=1, can_delete=True, min_num=1, validate_min=True
)


@owner_required
def item_edit(request, pk=None):
    obj = get_object_or_404(Item, pk=pk) if pk else Item()
    initial = {}
    if not pk and request.GET.get("category"):
        initial["category"] = request.GET["category"]
    form = ItemForm(request.POST or None, instance=obj, initial=initial)
    formset = VariantFormSet(request.POST or None, instance=obj, prefix="v")
    if not pk and request.method != "POST":
        formset = VariantFormSet(instance=obj, prefix="v", initial=[{"name": "Regular", "sort_order": 0}])
    if request.method == "POST":
        if "delete" in request.POST and pk:
            # Old bills keep their own copy of names and prices, so deleting is safe.
            obj.delete()
            messages.success(request, "Item deleted. Old bills are not affected.")
            return redirect("menu")
        if form.is_valid() and formset.is_valid():
            alive = [f for f in formset.forms if f.cleaned_data and not f.cleaned_data.get("DELETE")]
            if not alive:
                messages.error(request, "An item needs at least one size with a price.")
            else:
                with transaction.atomic():
                    item = form.save()
                    formset.instance = item
                    formset.save()
                messages.success(request, f"Saved {item.name}.")
                return redirect("menu")
    return render(request, "menu/item_form.html", {"form": form, "formset": formset, "obj": obj if pk else None})


@require_POST
def toggle(request, pk, field):
    """Quick switches on the menu page: active / sold out."""
    item = get_object_or_404(Item, pk=pk)
    user = request.user
    if field == "is_sold_out":
        if not (user.is_owner or ShopSettings.load().staff_can_mark_sold_out):
            raise PermissionDenied
    elif field == "is_active":
        if not user.is_owner:
            raise PermissionDenied
    else:
        raise PermissionDenied
    setattr(item, field, not getattr(item, field))
    item.save()
    if request.headers.get("HX-Request"):
        return render(request, "menu/_toggle.html", {"item": item, "field": field})
    return redirect(request.META.get("HTTP_REFERER") or "menu")
