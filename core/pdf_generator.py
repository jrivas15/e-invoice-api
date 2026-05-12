"""
Genera el PDF de una factura electrónica DIAN usando ReportLab.
Todo en memoria (BytesIO), sin escritura a disco.
"""
import io
from decimal import Decimal

import pytz
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, HRFlowable,
)

BOGOTA_TZ = pytz.timezone('America/Bogota')

GRAY_HEADER = colors.HexColor('#2C3E50')
GRAY_LIGHT  = colors.HexColor('#ECF0F1')
GRAY_MID    = colors.HexColor('#BDC3C7')
ACCENT      = colors.HexColor('#2980B9')


def _cop(amount) -> str:
    """Formatea un Decimal/float como pesos colombianos: $ 1.234.567,89"""
    try:
        val = float(amount)
    except (TypeError, ValueError):
        val = 0.0
    formatted = f'{val:,.2f}'
    # "1,234,567.89" → "1.234.567,89"
    return '$ ' + formatted.replace(',', 'X').replace('.', ',').replace('X', '.')


def generate_invoice_pdf(invoice, config) -> bytes:
    """
    Genera el PDF de la factura y retorna los bytes.

    Parameters
    ----------
    invoice : Invoice (debe estar ACCEPTED, con cufe, qr_data, items, customer)
    config  : FiscalConfig del tenant

    Returns
    -------
    bytes del PDF
    """
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=letter,
        leftMargin=1.5 * cm,
        rightMargin=1.5 * cm,
        topMargin=1.5 * cm,
        bottomMargin=1.5 * cm,
    )

    styles = getSampleStyleSheet()
    style_normal  = styles['Normal']
    style_small   = ParagraphStyle('small',  parent=style_normal, fontSize=7.5)
    style_tiny    = ParagraphStyle('tiny',   parent=style_normal, fontSize=6.5)
    style_bold    = ParagraphStyle('bold',   parent=style_normal, fontName='Helvetica-Bold')
    style_h1      = ParagraphStyle('h1',     parent=style_normal, fontName='Helvetica-Bold',
                                   fontSize=13, textColor=GRAY_HEADER)
    style_label   = ParagraphStyle('label',  parent=style_normal, fontSize=7.5,
                                   textColor=colors.HexColor('#7F8C8D'))
    style_mono    = ParagraphStyle('mono',   parent=style_normal, fontName='Courier',
                                   fontSize=6, wordWrap='CJK')

    story = []

    # -------------------------------------------------------------------------
    # HEADER: empresa | título + número factura
    # -------------------------------------------------------------------------
    company_name = Paragraph(config.legal_name, style_h1)
    company_info = Paragraph(
        f'NIT: {config.nit}-{config.check_digit}<br/>'
        f'{config.address or ""}<br/>'
        f'{config.city_name}, {config.department_code}<br/>'
        f'{config.phone or ""}<br/>'
        f'{config.email}',
        style_small,
    )
    invoice_title = Paragraph(
        '<font color="#2980B9"><b>FACTURA ELECTRÓNICA DE VENTA</b></font>',
        ParagraphStyle('inv_title', parent=style_normal, fontSize=11,
                       alignment=2, fontName='Helvetica-Bold'),
    )
    invoice_number = Paragraph(
        f'<b>No. {invoice.full_number}</b>',
        ParagraphStyle('inv_num', parent=style_normal, fontSize=10, alignment=2),
    )
    invoice_date_str = str(invoice.invoice_date) if invoice.invoice_date else ''
    invoice_date_p = Paragraph(
        f'Fecha: {invoice_date_str}',
        ParagraphStyle('inv_date', parent=style_normal, fontSize=8, alignment=2),
    )

    header_table = Table(
        [[company_name, invoice_title],
         [company_info, invoice_number],
         ['',           invoice_date_p]],
        colWidths=[10 * cm, None],
    )
    header_table.setStyle(TableStyle([
        ('VALIGN',    (0, 0), (-1, -1), 'TOP'),
        ('SPAN',      (0, 0), (0, 0)),
        ('TOPPADDING',    (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
    ]))
    story.append(header_table)
    story.append(HRFlowable(width='100%', thickness=1.5, color=ACCENT, spaceAfter=6))

    # -------------------------------------------------------------------------
    # DATOS CLIENTE
    # -------------------------------------------------------------------------
    customer = invoice.customer or {}
    doc_type_map = {'13': 'CC', '31': 'NIT', '22': 'CE', '91': 'NUIP', '12': 'TI'}
    doc_type_code = str(customer.get('document_type', '31'))
    doc_type_label = doc_type_map.get(doc_type_code, doc_type_code)
    customer_info = Paragraph(
        f'<b>Cliente:</b> {customer.get("legal_name", "")}<br/>'
        f'<b>{doc_type_label}:</b> {customer.get("document_number", "")}<br/>'
        f'<b>Dirección:</b> {customer.get("address", "")}<br/>'
        f'<b>Ciudad:</b> {customer.get("city_name", "")}<br/>'
        f'<b>Email:</b> {customer.get("email", "")}',
        style_small,
    )
    customer_table = Table([[customer_info]], colWidths=['100%'])
    customer_table.setStyle(TableStyle([
        ('BOX',           (0, 0), (-1, -1), 0.5, GRAY_MID),
        ('BACKGROUND',    (0, 0), (-1, -1), GRAY_LIGHT),
        ('TOPPADDING',    (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ('LEFTPADDING',   (0, 0), (-1, -1), 8),
    ]))
    story.append(customer_table)
    story.append(Spacer(1, 0.3 * cm))

    # -------------------------------------------------------------------------
    # TABLA DE ÍTEMS
    # -------------------------------------------------------------------------
    col_headers = ['#', 'Descripción', 'Cant.', 'Precio unit.', 'Dto.', 'Subtotal']
    item_rows = [col_headers]
    items = invoice.items or []
    for idx, item in enumerate(items, start=1):
        qty       = Decimal(str(item.get('quantity', 0)))
        unit_p    = Decimal(str(item.get('unit_price', 0)))
        discount  = Decimal(str(item.get('discount', 0)))
        subtotal  = qty * unit_p - discount
        item_rows.append([
            str(idx),
            item.get('description', ''),
            str(qty.normalize()),
            _cop(unit_p),
            _cop(discount),
            _cop(subtotal),
        ])

    page_width = letter[0] - 3 * cm
    items_table = Table(
        item_rows,
        colWidths=[0.8 * cm, None, 1.5 * cm, 3.2 * cm, 2.5 * cm, 3.2 * cm],
    )
    item_style = TableStyle([
        ('BACKGROUND',    (0, 0), (-1, 0),  GRAY_HEADER),
        ('TEXTCOLOR',     (0, 0), (-1, 0),  colors.white),
        ('FONTNAME',      (0, 0), (-1, 0),  'Helvetica-Bold'),
        ('FONTSIZE',      (0, 0), (-1, -1), 8),
        ('ALIGN',         (2, 0), (-1, -1), 'RIGHT'),
        ('ALIGN',         (0, 0), (1, -1),  'LEFT'),
        ('ROWBACKGROUNDS',(0, 1), (-1, -1), [colors.white, GRAY_LIGHT]),
        ('GRID',          (0, 0), (-1, -1), 0.3, GRAY_MID),
        ('TOPPADDING',    (0, 0), (-1, -1), 3),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
        ('LEFTPADDING',   (0, 0), (-1, -1), 4),
        ('RIGHTPADDING',  (0, 0), (-1, -1), 4),
    ])
    items_table.setStyle(item_style)
    story.append(items_table)
    story.append(Spacer(1, 0.3 * cm))

    # -------------------------------------------------------------------------
    # TOTALES
    # -------------------------------------------------------------------------
    totals_data = [
        ['Subtotal:', _cop(invoice.subtotal)],
        ['Descuentos:', _cop(invoice.discounts)],
        ['Impuestos:', _cop(invoice.taxes)],
        ['TOTAL A PAGAR:', _cop(invoice.total)],
    ]
    totals_table = Table(totals_data, colWidths=[5 * cm, 4 * cm], hAlign='RIGHT')
    totals_table.setStyle(TableStyle([
        ('ALIGN',         (0, 0), (-1, -1), 'RIGHT'),
        ('FONTSIZE',      (0, 0), (-1, -1), 9),
        ('FONTNAME',      (0, 3), (-1, 3),  'Helvetica-Bold'),
        ('FONTSIZE',      (0, 3), (-1, 3),  10),
        ('LINEABOVE',     (0, 3), (-1, 3),  1, GRAY_HEADER),
        ('TOPPADDING',    (0, 0), (-1, -1), 2),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
    ]))
    story.append(totals_table)
    story.append(HRFlowable(width='100%', thickness=0.5, color=GRAY_MID, spaceBefore=6, spaceAfter=6))

    # -------------------------------------------------------------------------
    # FOOTER: QR + CUFE
    # -------------------------------------------------------------------------
    footer_cells = []

    # QR code — usa qr_data del modelo; si está vacío, construye la URL DIAN
    qr_data = getattr(invoice, 'qr_data', '') or ''
    if not qr_data and invoice.cufe:
        if getattr(config, 'ambiente', 'PRUEBAS') == 'PRODUCCIÓN':
            qr_data = (
                f'https://catalogo-vpfe.dian.gov.co/Document/FindDocument'
                f'?documentKey={invoice.cufe}'
            )
        else:
            qr_data = (
                f'https://catalogo-vpfe-hab.dian.gov.co/Document/FindDocument'
                f'?documentKey={invoice.cufe}'
            )

    if qr_data:
        try:
            import qrcode
            from reportlab.platypus import Image as RLImage

            qr_img_pil = qrcode.make(qr_data)
            qr_buf = io.BytesIO()
            qr_img_pil.save(qr_buf, format='PNG')
            qr_buf.seek(0)
            qr_img = RLImage(qr_buf, width=2.5 * cm, height=2.5 * cm)
            footer_cells.append(qr_img)
        except Exception:
            footer_cells.append(Paragraph('(QR no disponible)', style_tiny))
    else:
        footer_cells.append(Paragraph('', style_tiny))

    cufe_short = (invoice.cufe or '')[:40] + '...' if len(invoice.cufe or '') > 40 else (invoice.cufe or '')
    cufe_block = Paragraph(
        f'<b>CUFE:</b><br/>'
        f'<font name="Courier" size="6">{invoice.cufe or ""}</font><br/><br/>'
        f'<font size="7" color="#27AE60"><b>✓ Documento validado por la DIAN</b></font>',
        style_small,
    )
    footer_cells.append(cufe_block)

    footer_table = Table([footer_cells], colWidths=[3 * cm, None])
    footer_table.setStyle(TableStyle([
        ('VALIGN',  (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING',  (0, 0), (0, -1), 0),
        ('RIGHTPADDING', (0, 0), (0, -1), 8),
    ]))
    story.append(footer_table)

    doc.build(story)
    return buf.getvalue()
