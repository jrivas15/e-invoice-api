import threading

from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.invoices.models import Invoice
from apps.invoices.repository import InvoiceRepository
from apps.invoices.services.invoice_service import get_next_number, process_invoice
from apps.tenants.models import Certificate, FiscalConfig
from apps.tenants.throttling import TenantInvoiceThrottle


class InvoiceListView(APIView):
    def get_throttles(self):
        if self.request.method == 'POST':
            return [TenantInvoiceThrottle()]
        return []

    def post(self, request):
        """Create invoice → queue for async processing."""
        tenant = request.tenant
        config = FiscalConfig.objects.get(tenant=tenant)
        cert = Certificate.objects.get(tenant=tenant, active=True)

        full_number, number = get_next_number(tenant)
        print(request.data)
        invoice = Invoice.objects.create(
            tenant=tenant,
            certificate=cert,
            prefix=config.invoice_prefix,
            number=number,
            full_number=full_number,
            invoice_date=timezone.now().date(),
            customer=request.data.get('customer'),
            items=request.data.get('items'),
            subtotal=request.data.get('subtotal'),
            discounts=request.data.get('discounts', 0),
            taxes=request.data.get('taxes', 0),
            total=request.data.get('total'),
            payment_means_code=request.data.get('payment_means_code', '10'),
            external_reference=request.data.get('external_reference', ''),
        )

        threading.Thread(target=process_invoice, args=(str(invoice.id),)).start()

        return Response({
            'id': str(invoice.id),
            'full_number': invoice.full_number,
            'status': invoice.status,
            'message': 'Factura recibida y en procesamiento',
        }, status=status.HTTP_201_CREATED)

    def get(self, request):
        """List invoices for this tenant — ordered by created_at desc, paginated."""
        try:
            page     = max(1, int(request.query_params.get('page', 1)))
            per_page = min(100, max(1, int(request.query_params.get('per_page', 20))))
        except (ValueError, TypeError):
            page, per_page = 1, 20

        repo    = InvoiceRepository(request.tenant)
        filters = {}
        status_filter = request.query_params.get('status')
        search = request.query_params.get('search', '').strip()
        if status_filter:
            filters['status'] = status_filter
        if search:
            filters['full_number__icontains'] = search
        qs    = repo.get_all(**filters)
        total = qs.count()
        offset   = (page - 1) * per_page
        invoices = qs[offset: offset + per_page]

        data = [
            {
                'id': str(i.id),
                'full_number': i.full_number,
                'status': i.status,
                'total': str(i.total),
                'customer_name': (i.customer or {}).get('legal_name'),
                'external_reference': i.external_reference or None,
                'created_at': i.created_at,
            }
            for i in invoices
        ]
        return Response({
            'results':   data,
            'total':     total,
            'page':      page,
            'per_page':  per_page,
            'pages':     -(-total // per_page),  # ceil division
        })


STATUS_MESSAGES = {
    'pending':    'Factura recibida, pendiente de envío a la DIAN',
    'processing': 'Factura en proceso de envío a la DIAN',
    'sent':       'Factura enviada a la DIAN, esperando respuesta',
    'accepted':   'Factura aceptada por la DIAN',
    'rejected':   'Factura rechazada por la DIAN',
    'error':      'Error interno al procesar la factura',
}


class InvoiceDetailView(APIView):

    def get(self, request, invoice_id):
        repo = InvoiceRepository(request.tenant)
        invoice = repo.get_by_id(invoice_id)
        if not invoice:
            return Response({'error': 'Not found'}, status=status.HTTP_404_NOT_FOUND)
        dian = invoice.dian_response or {}
        return Response({
            'id':               str(invoice.id),
            'full_number':      invoice.full_number,
            'status':           invoice.status,
            'message':          STATUS_MESSAGES.get(invoice.status, invoice.status),
            'dian_code':        dian.get('code'),
            'dian_status':      dian.get('status_description'),
            'dian_message':     dian.get('status_message'),
            'notifications':    dian.get('notifications', []),
            'validation_lines': dian.get('validation_lines', []),
            'cufe':             invoice.cufe,
            'customer':         invoice.customer or None,
            'items':            invoice.items or [],
            'subtotal':         str(invoice.subtotal),
            'discounts':        str(invoice.discounts),
            'taxes':            str(invoice.taxes),
            'total':            str(invoice.total),
            'external_reference': invoice.external_reference or None,
            'processed_at':     invoice.processed_at,
            'created_at':       invoice.created_at,
        })


class InvoiceResendEmailView(APIView):

    def post(self, request, invoice_id):
        """Reenvía el email de la factura al cliente. Usa GetStatus de la DIAN para obtener el ApplicationResponse."""
        repo = InvoiceRepository(request.tenant)
        invoice = repo.get_by_id(invoice_id)
        if not invoice:
            return Response({'error': 'Not found'}, status=status.HTTP_404_NOT_FOUND)
        if invoice.status != Invoice.Status.ACCEPTED:
            return Response(
                {'error': 'Solo se pueden reenviar facturas aceptadas por la DIAN'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        override_email = (request.data.get('email') or '').strip()

        from core.email_service import send_invoice_email
        config = FiscalConfig.objects.get(tenant=request.tenant)
        ok = send_invoice_email(invoice, config, override_email=override_email or None)

        if ok:
            recipient = override_email or (invoice.customer or {}).get('email', '')
            return Response({'message': f'Email reenviado para {invoice.full_number}', 'recipient': recipient})
        return Response(
            {'error': 'No se pudo enviar el email. Revisa los logs y que el cliente tenga email registrado.'},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
