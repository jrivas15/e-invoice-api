from django.contrib import admin
from django.urls import path, include
from django.http import JsonResponse

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/v1/', include('apps.invoices.urls')),
    path('health/', lambda r: JsonResponse({'ok': True})),
]
