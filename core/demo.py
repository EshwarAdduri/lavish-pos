"""
Demo mode — a safe playground for visitors (portfolio / testing).

How it works, in plain words:
  * When someone taps "Try the demo", we copy a ready-made sample database file (SQLite)
    into a NEW private file just for that visitor: demo_data/sandboxes/<time>_<id>.sqlite3
  * For every request from that visitor, Django is pointed at THEIR file instead of the
    real Supabase database. The real shop data is never read or written.
  * After DEMO_MINUTES (15) the file is deleted. Nothing is left behind.
  * Abuse limits: max sandboxes at once, max demos per IP per hour, max new orders per demo,
    max orders per minute, and a file-size cap.
"""

import contextlib
import hashlib
import json
import os
import re
import shutil
import sqlite3
import threading
import time
import uuid
from collections import defaultdict, deque
from datetime import date
from pathlib import Path

from django.conf import settings
from django.core import signing
from django.db import connections
from django.db.utils import load_backend
from django.http import HttpResponse
from django.shortcuts import redirect

COOKIE = "ls_demo"
# Demo visitors use their own login/device cookies, so trying the demo on the shop's phone
# never logs you out of the real shop.
COOKIE_SWAPS = [(None, "ls_demo_session"), ("ls_device", "ls_demo_device")]  # None = SESSION_COOKIE_NAME
_state = threading.local()
_build_lock = threading.Lock()
_ip_hits: dict = defaultdict(deque)
_last_cleanup = [0.0]


def enabled() -> bool:
    return getattr(settings, "DEMO_ENABLED", True)


def minutes() -> int:
    return int(getattr(settings, "DEMO_MINUTES", 15))


def demo_dir() -> Path:
    return Path(getattr(settings, "DEMO_DIR", settings.BASE_DIR / "demo_data"))


def template_path() -> Path:
    return demo_dir() / "template.sqlite3"


def sandbox_dir() -> Path:
    d = demo_dir() / "sandboxes"
    d.mkdir(parents=True, exist_ok=True)
    return d


def is_demo() -> bool:
    return getattr(_state, "sandbox", None) is not None


def current() -> dict | None:
    return getattr(_state, "sandbox", None)


# ------------------------------------------------------------------ point Django at a SQLite file
@contextlib.contextmanager
def use_sqlite(path: Path):
    """Inside this block, EVERY database query in this thread goes to `path` instead of the real database."""
    cfg = connections.configure_settings(
        {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": str(path), "OPTIONS": {"timeout": 20}}}
    )["default"]
    wrapper = load_backend(cfg["ENGINE"]).DatabaseWrapper(cfg, "default")
    previous = getattr(connections._connections, "default", None)
    connections["default"] = wrapper
    try:
        yield wrapper
    finally:
        try:
            wrapper.close()
        finally:
            if previous is None:
                with contextlib.suppress(AttributeError):
                    del connections["default"]
            else:
                connections["default"] = previous


# ------------------------------------------------------------------ the sample database (template)
def _migrations_fingerprint() -> str:
    from django.db.migrations.loader import MigrationLoader

    names = sorted(f"{a}.{n}" for a, n in MigrationLoader(None, ignore_no_migrations=True).disk_migrations)
    return hashlib.md5("|".join(names).encode()).hexdigest()


def _meta_path() -> Path:
    return demo_dir() / "template.json"


def template_ok() -> bool:
    try:
        meta = json.loads(_meta_path().read_text())
        return template_path().exists() and meta.get("fingerprint") == _migrations_fingerprint()
    except (OSError, ValueError):
        return False


def ensure_template():
    if template_ok():
        return
    with _build_lock:
        if template_ok():
            return
        from core.management.commands.build_demo import build

        build()


# ------------------------------------------------------------------ sandboxes
_NAME_RE = re.compile(r"^(\d{10})_([0-9a-f]{32})\.sqlite3$")


def sandbox_file(created: int, sid: str) -> Path:
    return sandbox_dir() / f"{created}_{sid}.sqlite3"


def _safe_delete(path: Path):
    for p in (path, Path(str(path) + "-journal"), Path(str(path) + "-wal"), Path(str(path) + "-shm")):
        with contextlib.suppress(OSError):
            p.unlink()


def cleanup(force: bool = False):
    """Delete expired sandboxes. Cheap; runs at most once a minute per worker."""
    now = time.time()
    if not force and now - _last_cleanup[0] < 60:
        return
    _last_cleanup[0] = now
    limit = minutes() * 60 + 120  # small grace period
    files = []
    for f in sandbox_dir().glob("*.sqlite3"):
        m = _NAME_RE.match(f.name)
        if not m:
            continue
        created = int(m.group(1))
        if now - created > limit:
            _safe_delete(f)
        else:
            files.append((created, f))
    # Too many at once? Remove the oldest.
    max_live = int(getattr(settings, "DEMO_MAX_SANDBOXES", 40))
    files.sort()
    while len(files) >= max_live:
        _safe_delete(files.pop(0)[1])


def _shift_dates(path: Path, days: int):
    """Move the sample history forward so 'yesterday' in the demo is always the real yesterday."""
    if days <= 0:
        return
    d = f"+{days} days"
    cols = {
        "orders_order": ["business_date:date", "created_at", "updated_at", "preparing_at", "ready_at", "completed_at", "cancelled_at"],
        "orders_payment": ["business_date:date", "created_at"],
        "orders_approvalrequest": ["created_at", "updated_at", "decided_at"],
        "stock_stockmovement": ["business_date:date", "created_at"],
        "stock_stockcount": ["business_date:date", "created_at", "updated_at"],
        "money_expense": ["business_date:date", "created_at"],
        "money_dayclose": ["business_date:date", "closed_at"],
        "core_auditlog": ["created_at"],
    }
    con = sqlite3.connect(path)
    try:
        for table, cs in cols.items():
            sets = []
            for c in cs:
                name, _, kind = c.partition(":")
                fn = "date" if kind == "date" else "datetime"
                sets.append(f"{name} = {fn}({name}, '{d}')")
            con.execute(f"UPDATE {table} SET {', '.join(sets)}")
        # daily token counters are keyed by date ("token:2026-10-01") — move them too
        con.execute("UPDATE orders_counter SET key = 'tmp:' || key WHERE key LIKE 'token:%'")
        con.execute(f"UPDATE orders_counter SET key = 'token:' || date(substr(key, 11), '{d}') WHERE key LIKE 'tmp:token:%'")
        con.commit()
    finally:
        con.close()


def allow_ip(ip: str) -> bool:
    per_hour = int(getattr(settings, "DEMO_PER_IP_PER_HOUR", 8))
    q = _ip_hits[ip or "?"]
    now = time.time()
    while q and now - q[0] > 3600:
        q.popleft()
    if len(q) >= per_hour:
        return False
    q.append(now)
    return True


def create_sandbox() -> tuple[int, str]:
    ensure_template()
    cleanup(force=True)
    created, sid = int(time.time()), uuid.uuid4().hex
    path = sandbox_file(created, sid)
    tmp = path.with_suffix(".tmp")
    shutil.copyfile(template_path(), tmp)
    meta = json.loads(_meta_path().read_text())
    from django.utils import timezone

    _shift_dates(tmp, (timezone.localdate() - date.fromisoformat(meta["built_on"])).days)
    os.replace(tmp, path)
    return created, sid


def cookie_value(created: int, sid: str) -> str:
    return signing.dumps({"c": created, "s": sid}, salt="lavish-demo")


def read_cookie(request) -> tuple[int, str] | None:
    raw = request.COOKIES.get(COOKIE)
    if not raw:
        return None
    try:
        data = signing.loads(raw, salt="lavish-demo", max_age=minutes() * 60 + 600)
        created, sid = int(data["c"]), str(data["s"])
    except (signing.BadSignature, KeyError, TypeError, ValueError):
        return None
    if not re.fullmatch(r"[0-9a-f]{32}", sid):
        return None
    return created, sid


def set_cookie(response, created: int, sid: str):
    response.set_cookie(COOKIE, cookie_value(created, sid), max_age=minutes() * 60 + 60,
                        httponly=True, samesite="Lax", secure=not settings.DEBUG)


# ------------------------------------------------------------------ order limits inside a demo
def check_order_limits():
    """Called before creating an order. Stops 'click 1000 orders' abuse in the demo."""
    sb = current()
    if not sb:
        return
    from datetime import datetime, timedelta, timezone as dt_tz

    from django.utils import timezone

    from orders.models import Order
    from orders.services import OrderError

    started = datetime.fromtimestamp(sb["created"], tz=dt_tz.utc)
    new_orders = Order.objects.filter(created_at__gte=started).count()
    if new_orders >= int(getattr(settings, "DEMO_MAX_NEW_ORDERS", 60)):
        raise OrderError("Demo limit reached (60 new orders). Tap “Restart demo” for a fresh copy.")
    recent = Order.objects.filter(created_at__gte=timezone.now() - timedelta(seconds=60)).count()
    if recent >= int(getattr(settings, "DEMO_MAX_ORDERS_PER_MINUTE", 12)):
        raise OrderError("Slow down a little — the demo allows 12 orders per minute.")


# ------------------------------------------------------------------ middleware
class DemoMiddleware:
    """Must sit BEFORE SessionMiddleware so even the login session lives in the visitor's sandbox."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.demo = None
        if not enabled():
            return self.get_response(request)
        cleanup()
        info = read_cookie(request)
        if info is None:
            if request.COOKIES.get(COOKIE):
                resp = self.get_response(request)
                resp.delete_cookie(COOKIE)
                return resp
            return self.get_response(request)

        created, sid = info
        path = sandbox_file(created, sid)
        expires = created + minutes() * 60
        if time.time() > expires or not path.exists():
            _safe_delete(path)
            resp = redirect("/login/?demo=ended")
            resp.delete_cookie(COOKIE)
            return resp
        max_bytes = int(getattr(settings, "DEMO_MAX_MB", 25)) * 1024 * 1024
        if request.method == "POST" and path.stat().st_size > max_bytes and not request.path.startswith("/demo/"):
            return HttpResponse("This demo is full. Tap “Restart demo” for a fresh copy.", status=429)

        request.demo = {"created": created, "sid": sid, "expires": expires}
        request._demo_delete = []
        swaps = [(real or settings.SESSION_COOKIE_NAME, alias) for real, alias in COOKIE_SWAPS]
        for real, alias in swaps:
            request.COOKIES.pop(real, None)
            if alias in request.COOKIES:
                request.COOKIES[real] = request.COOKIES[alias]
        _state.sandbox = request.demo
        try:
            with use_sqlite(path):
                response = self.get_response(request)
        finally:
            _state.sandbox = None
        for real, alias in swaps:
            morsel = response.cookies.pop(real, None) if hasattr(response, "cookies") else None
            if morsel is not None:
                response.cookies[alias] = morsel.value
                for key in morsel.keys():
                    if morsel[key]:
                        response.cookies[alias][key] = morsel[key]
        # Files can only be deleted after the connection is closed (important on Windows).
        for p in request._demo_delete:
            _safe_delete(p)
        return response
