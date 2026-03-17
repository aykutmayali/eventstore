from django.contrib import admin
from django.http import HttpResponse, JsonResponse
from django.urls import include, path
from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularRedocView,
    SpectacularSwaggerView,
)
from prometheus_client import generate_latest


def healthz(request):
    return JsonResponse({"status": "ok"})


def metrics(request):
    return HttpResponse(generate_latest(), content_type="text/plain; charset=utf-8")


urlpatterns = [
    path("admin/", admin.site.urls),
    # Health check
    path("healthz/", healthz, name="healthz"),
    # Prometheus metrics
    path("metrics/", metrics, name="metrics"),
    # OpenAPI
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path(
        "api/schema/swagger-ui/",
        SpectacularSwaggerView.as_view(url_name="schema"),
        name="swagger-ui",
    ),
    path(
        "api/schema/redoc/",
        SpectacularRedocView.as_view(url_name="schema"),
        name="redoc",
    ),
    # App URLs
    path("api/", include("apps.products.urls")),
    path("api/", include("apps.customers.urls")),
    path("api/", include("apps.inventory.urls")),
    path("api/", include("apps.orders.urls")),
]
