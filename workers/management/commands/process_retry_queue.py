from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.invoices.models import RetryQueue
from apps.invoices.services.invoice_service import process_invoice


class Command(BaseCommand):
    help = 'Process pending retry queue entries'

    def handle(self, *args, **options):
        pending = RetryQueue.objects.filter(
            status=RetryQueue.QueueStatus.PENDING,
            next_execution__lte=timezone.now()
        ).select_related('invoice')

        count = pending.count()
        for entry in pending:
            entry.status = RetryQueue.QueueStatus.PROCESSING
            entry.save(update_fields=['status'])
            process_invoice(str(entry.invoice.id))

        self.stdout.write(f'Processed {count} entries')
