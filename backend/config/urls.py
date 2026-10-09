from django.contrib import admin
from django.db import DatabaseError, connection
from django.http import FileResponse, HttpResponse, JsonResponse
from django.urls import include, path, re_path
from rest_framework.routers import DefaultRouter

from catalog.api import BOMViewSet, ItemViewSet
from production.api import AuditViewSet, PlanViewSet, ProductionViewSet, ReportViewSet
from sales.api import SalesViewSet

from .accounts import session, sign_in, sign_out
from .settings import BASE_DIR


def health(request):
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except DatabaseError:
        return JsonResponse({"status": "unavailable"}, status=503)
    return JsonResponse({"status": "ok"})


def index(request):
    page = BASE_DIR / "webdist" / "index.html"
    if page.is_file():
        return FileResponse(page.open("rb"), content_type="text/html")
    return HttpResponse("Django development server ready. Vue: http://127.0.0.1:5173")


router = DefaultRouter()
router.register("items", ItemViewSet)
router.register("boms", BOMViewSet)
router.register("sales-orders", SalesViewSet)
router.register("plans", PlanViewSet)
router.register("production-orders", ProductionViewSet)
router.register("reports", ReportViewSet)
router.register("audit", AuditViewSet)

urlpatterns = [
    path("api/v1/health/", health),
    path("api/v1/auth/session/", session),
    path("api/v1/auth/login/", sign_in),
    path("api/v1/auth/logout/", sign_out),
    path("api/v1/", include(router.urls)),
    path("admin/", admin.site.urls),
    re_path(r"^(?:|login/?|dashboard/?|sales(?:/.*)?|production(?:/.*)?|planning(?:/.*)?|catalog(?:/.*)?)$", index),
]
