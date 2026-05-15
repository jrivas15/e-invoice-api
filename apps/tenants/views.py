from rest_framework.response import Response
from rest_framework.views import APIView

from apps.tenants.models import FiscalConfig


class TenantConfigView(APIView):
    """GET /api/v1/config/ — devuelve la configuración fiscal del tenant.

    Fuente de verdad para que clientes (pos-cloud) no dupliquen datos
    como `ambiente` y se desincronicen.
    """

    def get(self, request):
        tenant = request.tenant
        try:
            config = FiscalConfig.objects.get(tenant=tenant)
        except FiscalConfig.DoesNotExist:
            return Response({'error': 'FiscalConfig no configurado'}, status=404)

        return Response({
            'tenant_id':      str(tenant.id),
            'tenant_name':    tenant.name,
            'ambiente':       config.ambiente,
            'nit':            config.nit,
            'check_digit':    config.check_digit,
            'legal_name':     config.legal_name,
            'trade_name':     config.trade_name,
            'invoice_prefix': config.invoice_prefix,
            'phone':          config.phone,
            'email':          config.email,
        })
