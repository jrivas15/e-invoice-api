from django.contrib import admin, messages
from django.http import HttpResponseRedirect
from django.urls import path, reverse
from django.utils.html import format_html

from apps.invoices.models import Invoice
from apps.tenants.models import FiscalConfig
from core.cert_service import load_certificate
from core.dian_client import send_to_test_set


@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    list_display    = (
        'full_number', 'tenant', 'document_type', 'status',
        'created_at', 'test_set_button',
    )
    list_filter     = ('status', 'document_type', 'tenant')
    search_fields   = ('full_number', 'cufe', 'external_reference')
    readonly_fields = (
        'id', 'tenant', 'certificate', 'document_type', 'prefix', 'number',
        'full_number', 'invoice_date', 'customer', 'items',
        'subtotal', 'discounts', 'taxes', 'total', 'currency',
        'payment_means_code', 'external_reference',
        'status', 'attempts', 'processed_at',
        'cufe', 'qr_data', 'signed_xml', 'dian_response',
        'created_at', 'updated_at',
    )
    ordering = ('-created_at',)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def test_set_button(self, obj):
        if not obj.signed_xml:
            return format_html('<span style="color:#999">— Sin XML firmado</span>')
        url = reverse('admin:invoice-send-test-set', args=[obj.pk])
        return format_html(
            '<a class="button" href="{}">📤 Set de pruebas</a>', url,
        )
    test_set_button.short_description = 'Set DIAN'

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                '<uuid:pk>/send-test-set/',
                self.admin_site.admin_view(self._send_test_set),
                name='invoice-send-test-set',
            ),
        ]
        return custom + urls

    def _send_test_set(self, request, pk):
        invoice = Invoice.objects.select_related('tenant').get(pk=pk)
        config  = FiscalConfig.objects.get(tenant=invoice.tenant)
        redirect = HttpResponseRedirect(
            reverse('admin:invoices_invoice_change', args=[pk])
        )

        if not config.test_set_id:
            self.message_user(
                request,
                'El tenant no tiene test_set_id configurado en FiscalConfig.',
                level=messages.ERROR,
            )
            return redirect

        if not invoice.signed_xml:
            self.message_user(
                request,
                'La factura no tiene XML firmado todavía. Espera a que se procese.',
                level=messages.ERROR,
            )
            return redirect

        p12, password = load_certificate(invoice.tenant)
        result = send_to_test_set(
            invoice.signed_xml, p12, password, config, config.test_set_id,
        )

        if result.get('code') == '00':
            self.message_user(
                request,
                format_html(
                    'Enviada al set DIAN. <strong>ZipKey:</strong> '
                    '<code>{}</code>',
                    result.get('zip_key', ''),
                ),
                level=messages.SUCCESS,
            )
        else:
            errs = '; '.join(result.get('errors', [])) or result.get('status_msg', 'Sin detalle')
            self.message_user(
                request,
                f'Error al enviar al set: {errs}',
                level=messages.ERROR,
            )

        return redirect
