import hashlib
from django.http import JsonResponse


class TenantAuthMiddleware:
    """
    Resolves X-API-Key header → tenant.
    Attaches tenant to request.tenant.
    Exempt paths: /admin/, /health/
    """
    EXEMPT_PATHS = ('/jmanage/', '/health/')

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if any(request.path.startswith(p) for p in self.EXEMPT_PATHS):
            return self.get_response(request)

        api_key = request.headers.get('X-API-Key', '')
        if not api_key:
            return JsonResponse({'error': 'Missing X-API-Key header'}, status=401)

        key_hash = hashlib.sha256(api_key.encode()).hexdigest()

        from apps.tenants.models import Tenant
        try:
            tenant = Tenant.objects.get(api_key_hash=key_hash, active=True)
        except Tenant.DoesNotExist:
            return JsonResponse({'error': 'Invalid or inactive API key'}, status=401)

        request.tenant = tenant
        return self.get_response(request)
