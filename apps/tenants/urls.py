from django.urls import path

from apps.tenants.views import TenantConfigView

urlpatterns = [
    path('config/', TenantConfigView.as_view()),
]
