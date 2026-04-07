from django.urls import path
from apps.invoices.views import InvoiceDetailView, InvoiceListView

urlpatterns = [
    path('invoices/', InvoiceListView.as_view()),
    path('invoices/<uuid:invoice_id>/', InvoiceDetailView.as_view()),
]
