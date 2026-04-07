from apps.invoices.models import Invoice


class InvoiceRepository:
    """
    All queries are always filtered by tenant_id.
    Never bypass this class to query Invoice directly.
    """

    def __init__(self, tenant):
        self.tenant = tenant

    def _base(self):
        return Invoice.objects.filter(tenant=self.tenant)

    def get_all(self, **filters):
        return self._base().filter(**filters).order_by('-created_at')

    def get_by_id(self, invoice_id):
        return self._base().filter(id=invoice_id).first()

    def get_by_external_ref(self, ref):
        return self._base().filter(external_reference=ref).first()

    def get_pending(self):
        return self._base().filter(status=Invoice.Status.PENDING)

    def create(self, **data):
        return Invoice.objects.create(tenant=self.tenant, **data)
