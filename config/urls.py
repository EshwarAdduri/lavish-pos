from django.contrib import admin
from django.urls import path

from accounts import views as acc
from core import demo_views
from core import upi
from core import views as core
from menu import views as menu
from money import views as money
from orders import views as orders
from stock import views as stock

admin.site.site_header = "Lavish Shawarma — backup admin"

urlpatterns = [
    path("", core.home, name="home"),
    path("healthz", core.healthz, name="healthz"),
    path("live/pulse/", core.pulse, name="pulse"),
    path("upi/qr.svg", upi.upi_qr, name="upi_qr"),
    path("manifest.webmanifest", core.manifest, name="manifest"),
    path("sw.js", core.service_worker, name="sw"),
    path("offline/", core.offline, name="offline"),

    # demo
    path("demo/start/", demo_views.start, name="demo_start"),
    path("demo/enter/", demo_views.enter, name="demo_enter"),
    path("demo/end/", demo_views.end, name="demo_end"),

    # accounts
    path("login/", acc.login_view, name="login"),
    path("logout/", acc.logout_view, name="logout"),
    path("account/password/", acc.change_password, name="change_password"),
    path("team/", acc.team, name="team"),
    path("team/users/new/", acc.user_edit, name="user_new"),
    path("team/users/<int:pk>/", acc.user_edit, name="user_edit"),
    path("team/devices/<uuid:pk>/", acc.device_edit, name="device_edit"),

    # POS & orders
    path("pos/", orders.pos, name="pos"),
    path("pos/menu.json", orders.pos_menu, name="pos_menu"),
    path("pos/recent.json", orders.pos_recent, name="pos_recent"),
    path("orders/<int:pk>/edit.json", orders.order_edit_json, name="order_edit_json"),
    path("orders/<int:pk>/pay.json", orders.order_pay_json, name="order_pay_json"),
    path("orders/<int:pk>/status.json", orders.order_status_json, name="order_status_json"),
    path("orders/new/", orders.order_create, name="order_create"),
    path("orders/", orders.orders_list, name="orders"),
    path("orders/board/", orders.board, name="board"),
    path("orders/<int:pk>/", orders.order_detail, name="order_detail"),
    path("orders/<int:pk>/update/", orders.order_update, name="order_update"),
    path("orders/<int:pk>/pay/", orders.order_pay, name="order_pay"),
    path("orders/<int:pk>/status/", orders.order_status, name="order_status"),
    path("orders/<int:pk>/cancel/", orders.order_cancel, name="order_cancel"),
    path("orders/<int:pk>/refund/", orders.order_refund, name="order_refund"),
    path("orders/<int:pk>/request/", orders.order_request, name="order_request"),
    path("orders/<int:pk>/receipt/", orders.receipt, name="receipt"),
    path("orders/<int:pk>/receipt.pdf", orders.receipt_pdf_view, name="receipt_pdf"),
    path("approvals/", orders.approvals, name="approvals"),
    path("approvals/<int:pk>/decide/", orders.approval_decide, name="approval_decide"),

    # menu
    path("menu/", menu.menu_home, name="menu"),
    path("menu/items/new/", menu.item_edit, name="item_new"),
    path("menu/items/<int:pk>/", menu.item_edit, name="item_edit"),
    path("menu/items/<int:pk>/toggle/<str:field>/", menu.toggle, name="item_toggle"),
    path("menu/categories/new/", menu.category_edit, name="category_new"),
    path("menu/categories/<int:pk>/", menu.category_edit, name="category_edit"),
    path("menu/extras/new/", menu.addon_edit, name="addon_new"),
    path("menu/extras/<int:pk>/", menu.addon_edit, name="addon_edit"),

    # stock
    path("stock/", stock.overview, name="stock"),
    path("stock/record/<str:kind>/", stock.record, name="stock_record"),
    path("stock/history/", stock.history, name="stock_history"),
    path("stock/history/<int:pk>/delete/", stock.movement_delete, name="stock_movement_delete"),
    path("stock/items/", stock.items, name="stock_items"),
    path("stock/items/new/", stock.item_edit, name="stock_item_new"),
    path("stock/items/<int:pk>/", stock.item_edit, name="stock_item_edit"),
    path("stock/recipes/", stock.recipes, name="stock_recipes"),
    path("stock/variance/", stock.variance_report, name="stock_variance"),
    path("stock/prep/", stock.prep_plan, name="prep_plan"),

    # money
    path("money/", money.dashboard, name="dashboard"),
    path("money/reports/", money.reports, name="reports"),
    path("money/export/<str:kind>", money.export, name="export"),
    path("money/expenses/", money.expenses, name="expenses"),
    path("money/expenses/new/", money.expense_edit, name="expense_new"),
    path("money/expenses/<int:pk>/", money.expense_edit, name="expense_edit"),
    path("money/close/", money.day_close, name="day_close"),
    path("money/closings/", money.closings, name="closings"),
    path("money/closings/<str:day>/", money.closing_report, name="closing_report"),

    # settings & audit
    path("settings/", core.shop_settings, name="settings"),
    path("audit/", core.audit_log, name="audit"),

    # Django's built-in admin, as an emergency backup tool for the owner.
    path("admin/", admin.site.urls),
]

handler403 = "core.views.forbidden"
handler404 = "core.views.not_found"
