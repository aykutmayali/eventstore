from rest_framework.routers import DefaultRouter

from apps.inventory.views import InventoryItemViewSet

router = DefaultRouter()
router.register("inventory", InventoryItemViewSet)

urlpatterns = router.urls
