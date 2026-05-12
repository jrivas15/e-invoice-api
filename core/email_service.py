"""
Servicio de email para facturas electrónicas DIAN.

Flujo normal (process_invoice):
  send_invoice_email(invoice, config, application_response_xml=ar_xml)
  → usa el ApplicationResponse ya disponible en memoria, sin consulta extra a DIAN.

Flujo de reenvío / contextos externos:
  send_invoice_email(invoice, config)
  → llama a get_application_response() (DIAN GetStatus) como fallback.
"""
import io
import logging
import zipfile
from pathlib import Path

import resend
from django.conf import settings

from core.attached_document_builder import build_attached_document
from core.pdf_generator import generate_invoice_pdf

logger = logging.getLogger(__name__)


def send_invoice_email(
    invoice,
    config,
    application_response_xml: str = '',
    override_email: str | None = None,
) -> bool:
    """
    Envía la factura por email con un ZIP adjunto que contiene:
      - factura_{fullNumber}.pdf
      - {fullNumber}_ad.xml  (AttachedDocument UBL 2.1)

    Parameters
    ----------
    override_email : Si se provee, se usa en lugar del email del cliente.
                     Útil para reenvíos a un destinatario diferente.

    Nunca lanza excepción — los errores se loguean y retornan False.
    """
    try:
        # 1. Determinar destinatario
        if override_email:
            recipient = override_email
        else:
            recipient = (invoice.customer or {}).get('email', '').strip()

        if not recipient:
            logger.warning(
                '[email] %s: sin email de destinatario, no se envía', invoice.full_number
            )
            return False

        # 2. ApplicationResponse — del flujo normal o consulta a DIAN como fallback
        ar_xml = application_response_xml
        if not ar_xml:
            logger.info(
                '[email] %s: application_response_xml no disponible, consultando DIAN GetStatus',
                invoice.full_number,
            )
            from core.cert_service import load_certificate
            from core.dian_client import get_application_response
            p12, password = load_certificate(invoice.tenant)
            ar_xml = get_application_response(invoice.cufe, p12, password, config)

        # 3. Generar documentos
        pdf_bytes = generate_invoice_pdf(invoice, config)
        ad_xml    = build_attached_document(invoice, config, ar_xml)

        # 4. Empaquetar ZIP en memoria
        zip_bytes = _build_zip(invoice.full_number, pdf_bytes, ad_xml)

        # 5. Enviar con Resend
        resend.api_key = settings.RESEND_API_KEY
        params: resend.Emails.SendParams = {
            'from':    settings.RESEND_FROM_EMAIL,
            'to':      [recipient],
            'subject': f'Factura Electrónica {invoice.full_number} — {config.legal_name}',
            'html':    _build_email_html(invoice, config),
            'attachments': [{
                'filename': f'factura_{invoice.full_number}.zip',
                'content':  list(zip_bytes),
            }],
        }
        result = resend.Emails.send(params)
        logger.info('[email] %s: enviado id=%s', invoice.full_number, result.get('id', '?'))
        return True

    except Exception as exc:
        logger.exception('[email] %s: error al enviar — %s', invoice.full_number, exc)
        return False


# ---------------------------------------------------------------------------
# Helpers privados
# ---------------------------------------------------------------------------

def _build_zip(full_number: str, pdf_bytes: bytes, ad_xml: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode='w', compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f'factura_{full_number}.pdf', pdf_bytes)
        zf.writestr(f'{full_number}_ad.xml', ad_xml.encode('utf-8'))
    return buf.getvalue()


_TEMPLATE_PATH = Path(__file__).parent / 'templates' / 'invoice_email.html'


def _build_email_html(invoice, config) -> str:
    customer_name = (invoice.customer or {}).get('legal_name', 'Cliente')
    cufe          = invoice.cufe or ''
    template      = _TEMPLATE_PATH.read_text(encoding='utf-8')
    return template.format(
        legal_name    = config.legal_name,
        customer_name = customer_name,
        full_number   = invoice.full_number,
        total         = _cop_html(invoice.total),
        cufe          = cufe,
    )


def _cop_html(amount) -> str:
    try:
        val = float(amount)
    except (TypeError, ValueError):
        val = 0.0
    formatted = f'{val:,.2f}'
    return '$ ' + formatted.replace(',', 'X').replace('.', ',').replace('X', '.')
