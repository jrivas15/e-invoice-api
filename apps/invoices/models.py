import uuid
from django.db import models
from apps.tenants.models import Tenant, Certificate


class Invoice(models.Model):

    class Status(models.TextChoices):
        PENDING    = 'pending',    'Pending'
        PROCESSING = 'processing', 'Processing'
        SENT       = 'sent',       'Sent to DIAN'
        ACCEPTED   = 'accepted',   'Accepted'
        REJECTED   = 'rejected',   'Rejected'
        ERROR      = 'error',      'Internal error'

    class DocumentType(models.TextChoices):
        SALES_INVOICE = 'FV', 'Sales invoice'
        CREDIT_NOTE   = 'NC', 'Credit note'
        DEBIT_NOTE    = 'ND', 'Debit note'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(
        Tenant, on_delete=models.PROTECT,
        related_name='invoices', db_index=True
    )
    certificate = models.ForeignKey(
        Certificate, on_delete=models.PROTECT, related_name='invoices'
    )

    document_type = models.CharField(
        max_length=2, choices=DocumentType.choices,
        default=DocumentType.SALES_INVOICE
    )
    prefix = models.CharField(max_length=10, blank=True)
    number = models.IntegerField()
    full_number = models.CharField(max_length=50, db_index=True)

    status = models.CharField(
        max_length=20, choices=Status.choices,
        default=Status.PENDING, db_index=True
    )
    attempts = models.IntegerField(default=0)
    processed_at = models.DateTimeField(null=True, blank=True)

    receiver = models.JSONField()
    items = models.JSONField()

    subtotal = models.DecimalField(max_digits=18, decimal_places=2)
    discounts = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    taxes = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    total = models.DecimalField(max_digits=18, decimal_places=2)
    currency = models.CharField(max_length=3, default='COP')

    payment_means_code = models.CharField(max_length=3, default='10')
    external_reference = models.CharField(max_length=100, blank=True, db_index=True)

    cufe = models.CharField(max_length=200, blank=True, db_index=True)
    qr_data = models.TextField(blank=True)
    signed_xml = models.TextField(blank=True)
    dian_response = models.JSONField(null=True, blank=True)


    invoice_date = models.DateField()
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'invoices'
        indexes = [
            models.Index(fields=['tenant', 'created_at']),
            models.Index(fields=['tenant', 'status']),
            models.Index(fields=['tenant', 'full_number']),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['tenant', 'document_type', 'prefix', 'number'],
                name='uq_invoice_number_tenant'
            )
        ]

    def __str__(self):
        return f"{self.full_number} — {self.tenant}"


class RetryQueue(models.Model):

    class QueueStatus(models.TextChoices):
        PENDING    = 'pending',    'Pending'
        PROCESSING = 'processing', 'Processing'
        EXHAUSTED  = 'exhausted',  'Retries exhausted'

    invoice = models.ForeignKey(
        Invoice, on_delete=models.CASCADE, related_name='retries'
    )
    status = models.CharField(
        max_length=20, choices=QueueStatus.choices,
        default=QueueStatus.PENDING
    )
    error_message = models.TextField()
    attempt_number = models.IntegerField()
    next_execution = models.DateTimeField(db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'retry_queue'
        indexes = [
            models.Index(fields=['status', 'next_execution']),
        ]


class MonthlyUsage(models.Model):
    tenant = models.ForeignKey(
        Tenant, on_delete=models.PROTECT, related_name='monthly_usage'
    )
    year = models.IntegerField()
    month = models.IntegerField()
    total_invoices = models.IntegerField(default=0)
    successful = models.IntegerField(default=0)
    failed = models.IntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'monthly_usage'
        constraints = [
            models.UniqueConstraint(
                fields=['tenant', 'year', 'month'],
                name='uq_monthly_usage_tenant'
            )
        ]
