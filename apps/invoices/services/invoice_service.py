from datetime import timedelta

from django.db.models import F
from django.utils import timezone

from apps.invoices.models import Invoice, RetryQueue, MonthlyUsage
from apps.tenants.models import FiscalConfig
from core.cert_service import load_certificate
from core.xml_builder import build_xml
from core.signer import sign_xml
from core.dian_client import send_to_dian


def get_next_number(tenant) -> tuple[str, int]:
    """Atomically increments invoice number. Race-condition safe."""
    FiscalConfig.objects.filter(tenant=tenant).update(
        current_number=F('current_number') + 1
    )
    config = FiscalConfig.objects.get(tenant=tenant)
    full = f"{config.invoice_prefix}{config.current_number}"
    return full, config.current_number


def process_invoice(invoice_id: str):
    """Called in background after POST /invoices."""
    invoice = Invoice.objects.select_related('tenant', 'certificate').get(id=invoice_id)
    tenant = invoice.tenant

    invoice.status = Invoice.Status.PROCESSING
    invoice.attempts += 1
    invoice.save(update_fields=['status', 'attempts'])

    try:
        config = FiscalConfig.objects.get(tenant=tenant)
        p12, password = load_certificate(tenant)

        xml, cufe, qr_data = build_xml(invoice, config)
        # print(f"Generated XML for invoice {invoice.id}:\n{xml}")
        signed_xml = sign_xml(xml, p12, password)

        with open(f"./invoice_{invoice.full_number}.xml", "w", encoding="utf-8") as f:
            f.write(signed_xml)

        response = send_to_dian(signed_xml, p12, password, config)
        # print(f"DIAN response for invoice {invoice.id}:\n{response}")

        if response['code'] == '00':
            invoice.status = Invoice.Status.ACCEPTED
        else:
            invoice.status = Invoice.Status.REJECTED

        invoice.signed_xml = signed_xml
        invoice.cufe = cufe
        invoice.qr_data = qr_data
        invoice.dian_response = response
        invoice.processed_at = timezone.now()
        invoice.save()

        _update_monthly_usage(tenant, success=(invoice.status == Invoice.Status.ACCEPTED))

        if invoice.status == Invoice.Status.ACCEPTED:
            print(f'[process_invoice] Invoice {invoice.id} accepted by DIAN. Attempting to send email...')
            try:
                from core.email_service import send_invoice_email
                ar_xml = response.get('application_response_xml', '')
                ok = send_invoice_email(invoice, config, application_response_xml=ar_xml)
                print(f'[process_invoice] Email sent for invoice {invoice.id} status={ok}')
            except Exception as email_exc:
                import traceback
                print(f'[process_invoice] Email failed for invoice={invoice_id}: {email_exc}')
                traceback.print_exc()

    except Exception as e:
        import traceback
        print(f'[process_invoice] ERROR invoice={invoice_id}: {e}')
        traceback.print_exc()
        invoice.status = Invoice.Status.ERROR
        invoice.save(update_fields=['status'])
        _schedule_retry(invoice, str(e))


def _schedule_retry(invoice: Invoice, error: str):
    """Exponential backoff: 5min, 15min, 45min, 2h, 6h (max 5 attempts)."""
    MAX_ATTEMPTS = 5
    if invoice.attempts >= MAX_ATTEMPTS:
        RetryQueue.objects.filter(invoice=invoice).update(
            status=RetryQueue.QueueStatus.EXHAUSTED
        )
        return

    delay_minutes = [5, 15, 45, 120, 360]
    delay = delay_minutes[min(invoice.attempts - 1, len(delay_minutes) - 1)]

    RetryQueue.objects.create(
        invoice=invoice,
        error_message=error,
        attempt_number=invoice.attempts,
        next_execution=timezone.now() + timedelta(minutes=delay)
    )


def _update_monthly_usage(tenant, success: bool):
    now = timezone.now()
    obj, _ = MonthlyUsage.objects.get_or_create(
        tenant=tenant, year=now.year, month=now.month
    )
    MonthlyUsage.objects.filter(pk=obj.pk).update(
        total_invoices=F('total_invoices') + 1,
        successful=F('successful') + (1 if success else 0),
        failed=F('failed') + (0 if success else 1),
    )
