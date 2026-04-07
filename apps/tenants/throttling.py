from rest_framework.throttling import SimpleRateThrottle


class TenantInvoiceThrottle(SimpleRateThrottle):
    """60 invoice POSTs per minute per tenant."""
    scope = 'tenant_invoice'

    def get_cache_key(self, request, view):
        tenant = getattr(request, 'tenant', None)
        if tenant is None:
            return None
        return self.cache_format % {
            'scope': self.scope,
            'ident': str(tenant.id),
        }
