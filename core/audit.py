"""
Audit trail.

`track(Model)` makes every create / update / delete of that model write an AuditLog row
with who did it, from which device, and exactly which fields changed (old -> new).
`log(...)` records business events such as logins, cancellations and approvals.
"""

from decimal import Decimal
from datetime import date, datetime

from django.db.models.signals import post_delete, post_save, pre_save

from .utils import client_ip, get_current_request

_tracked: dict = {}


def _plain(value):
    if isinstance(value, (Decimal,)):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if hasattr(value, "pk"):
        return value.pk
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _snapshot(instance, exclude):
    data = {}
    from django.db import models

    for f in instance._meta.concrete_fields:
        if f.name in exclude:
            continue
        value = getattr(instance, f.attname)
        if isinstance(f, models.DecimalField) and value is not None:
            try:  # 140 and 140.00 are the same price — don't report a fake change
                value = Decimal(str(value)).quantize(Decimal(1).scaleb(-f.decimal_places))
            except Exception:
                pass
        data[f.name] = _plain(value)
    return data


def log(action: str, obj=None, changes: dict | None = None, note: str = "", model: str | None = None, request=None):
    from .models import AuditLog

    request = request or get_current_request()
    user = getattr(request, "user", None) if request else None
    AuditLog.objects.create(
        actor=user if user is not None and user.is_authenticated else None,
        device=getattr(request, "device", None) if request else None,
        ip=client_ip(request),
        action=action,
        model=model or (obj._meta.verbose_name.title() if obj is not None else ""),
        object_id=str(obj.pk) if obj is not None and obj.pk is not None else "",
        object_repr=str(obj)[:200] if obj is not None else "",
        changes=changes or {},
        note=note[:300],
    )


def _pre_save(sender, instance, **kwargs):
    exclude, skip = _tracked[sender]
    if skip and skip(instance):
        return
    if instance.pk:
        old = sender.objects.filter(pk=instance.pk).first()
        instance._audit_old = _snapshot(old, exclude) if old else None
    else:
        instance._audit_old = None


def _post_save(sender, instance, created, **kwargs):
    exclude, skip = _tracked[sender]
    if skip and skip(instance):
        return
    new = _snapshot(instance, exclude)
    if created or instance.__dict__.get("_audit_old") is None:
        log("create", instance, {k: [None, v] for k, v in new.items() if v not in (None, "")})
        return
    old = instance._audit_old
    diff = {k: [old.get(k), v] for k, v in new.items() if old.get(k) != v}
    if diff:
        log("update", instance, diff)


def _post_delete(sender, instance, **kwargs):
    exclude, skip = _tracked[sender]
    if skip and skip(instance):
        return
    log("delete", instance, {k: [v, None] for k, v in _snapshot(instance, exclude).items()})


def track(model, exclude=("updated_at", "last_login", "password"), skip=None):
    """skip: optional function(instance) -> True to not log that row (e.g. automatic entries)."""
    _tracked[model] = (set(exclude), skip)
    pre_save.connect(_pre_save, sender=model, dispatch_uid=f"audit_pre_{model.__name__}")
    post_save.connect(_post_save, sender=model, dispatch_uid=f"audit_post_{model.__name__}")
    post_delete.connect(_post_delete, sender=model, dispatch_uid=f"audit_del_{model.__name__}")
