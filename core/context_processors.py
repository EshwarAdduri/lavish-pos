from django.conf import settings

from .models import ShopSettings


def shop(request):
    data = {"DEBUG": settings.DEBUG}
    if request.path.startswith("/static/"):
        return data
    data["shop"] = ShopSettings.load()
    user = getattr(request, "user", None)
    if user is not None and user.is_authenticated and user.is_owner:
        from orders.models import ApprovalRequest

        data["pending_approvals"] = ApprovalRequest.objects.filter(status=ApprovalRequest.Status.PENDING).count()
    return data
