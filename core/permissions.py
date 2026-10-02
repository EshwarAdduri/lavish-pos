from functools import wraps

from django.core.exceptions import PermissionDenied

from .models import ShopSettings


def owner_required(view):
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated or not request.user.is_owner:
            raise PermissionDenied("Owner only")
        return view(request, *args, **kwargs)

    return wrapper


def can_record(user) -> bool:
    """Recording stock purchases, wastage, expenses and day close."""
    if not user.is_authenticated:
        return False
    return user.is_owner or ShopSettings.load().staff_can_record


def recorder_required(view):
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not can_record(request.user):
            raise PermissionDenied("Not allowed")
        return view(request, *args, **kwargs)

    return wrapper
