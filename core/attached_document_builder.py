"""
Construye un AttachedDocument UBL 2.1 — contenedor estándar para entrega de
facturas electrónicas al cliente en el esquema DIAN colombiano.

El documento contiene:
  - El XML firmado de la factura (cac:Attachment/cbc:Description CDATA)
  - El ApplicationResponse de la DIAN (ParentDocumentLineReference CDATA)

No incluye firma digital del wrapper en v1; los documentos embebidos
ya llevan sus propias firmas (XAdES del emisor y firma de la DIAN).
"""
import uuid
from datetime import datetime

import pytz
from lxml import etree

BOGOTA_TZ = pytz.timezone('America/Bogota')

NS_AD  = 'urn:oasis:names:specification:ubl:schema:xsd:AttachedDocument-2'
NS_CAC = 'urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2'
NS_CBC = 'urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2'
NS_EXT = 'urn:oasis:names:specification:ubl:schema:xsd:CommonExtensionComponents-2'
NS_XSI = 'http://www.w3.org/2001/XMLSchema-instance'
NS_DS  = 'http://www.w3.org/2000/09/xmldsig#'
NS_XAD = 'http://uri.etsi.org/01903/v1.3.2#'

NSMAP = {
    None:    NS_AD,
    'cac':   NS_CAC,
    'cbc':   NS_CBC,
    'ext':   NS_EXT,
    'xsi':   NS_XSI,
    'ds':    NS_DS,
    'xades': NS_XAD,
}


def _cbc(tag):
    return f'{{{NS_CBC}}}{tag}'


def _cac(tag):
    return f'{{{NS_CAC}}}{tag}'


def build_attached_document(invoice, config, application_response_xml: str) -> str:
    """
    Construye el AttachedDocument UBL 2.1 para una factura aceptada por la DIAN.

    Parameters
    ----------
    invoice                  : Invoice (debe tener signed_xml, cufe, full_number,
                               customer, invoice_date, processed_at)
    config                   : FiscalConfig del tenant emisor
    application_response_xml : XML del ApplicationResponse de la DIAN (decodificado)

    Returns
    -------
    XML string UTF-8
    """
    now_bogota = datetime.now(BOGOTA_TZ)
    issue_date = now_bogota.strftime('%Y-%m-%d')
    issue_time = now_bogota.strftime('%H:%M:%S') + '-05:00'

    root = etree.Element(f'{{{NS_AD}}}AttachedDocument', nsmap=NSMAP)

    def _add(parent, tag, text=None, attrib=None):
        el = etree.SubElement(parent, tag, attrib or {})
        if text is not None:
            el.text = text
        return el

    _add(root, _cbc('UBLVersionID'), 'UBL 2.1')
    _add(root, _cbc('CustomizationID'), 'Documentos adjuntos')
    _add(root, _cbc('ProfileID'), 'DIAN 2.1')
    _add(root, _cbc('ProfileExecutionID'), '1')
    _add(root, _cbc('ID'), str(uuid.uuid4()))
    _add(root, _cbc('IssueDate'), issue_date)
    _add(root, _cbc('IssueTime'), issue_time)
    _add(root, _cbc('DocumentType'), 'Contenedor de Factura Electrónica de Venta')
    _add(root, _cbc('ParentDocumentID'), invoice.full_number)

    # --- SenderParty (emisor / tenant) ---
    sender = _add(root, _cac('SenderParty'))
    sender_pts = _add(sender, _cac('PartyTaxScheme'))
    _add(sender_pts, _cbc('RegistrationName'), config.legal_name)
    _add(sender_pts, _cbc('CompanyID'), config.nit, attrib={
        'schemeID':       config.check_digit,
        'schemeName':     '31',
        'schemeAgencyID': '195',
        'schemeAgencyName': 'CO, DIAN (Dirección de Impuestos y Aduanas Nacionales)',
    })
    tax_level = config.tax_responsibilities[0] if config.tax_responsibilities else 'R-99-PN'
    _add(sender_pts, _cbc('TaxLevelCode'), tax_level, attrib={'listName': '48'})
    sender_ts = _add(sender_pts, _cac('TaxScheme'))
    _add(sender_ts, _cbc('ID'), config.tax_scheme_id)
    _add(sender_ts, _cbc('Name'), config.tax_scheme_name)

    # --- ReceiverParty (cliente) ---
    customer = invoice.customer or {}
    receiver = _add(root, _cac('ReceiverParty'))
    receiver_pts = _add(receiver, _cac('PartyTaxScheme'))
    _add(receiver_pts, _cbc('RegistrationName'), customer.get('legal_name', ''))
    doc_type   = customer.get('document_type', '31')
    doc_number = customer.get('document_number', '')
    _add(receiver_pts, _cbc('CompanyID'), doc_number, attrib={
        'schemeName':     doc_type,
        'schemeAgencyID': '195',
    })
    receiver_ts = _add(receiver_pts, _cac('TaxScheme'))
    _add(receiver_ts, _cbc('ID'), customer.get('tax_scheme_id', 'ZZ'))
    _add(receiver_ts, _cbc('Name'), customer.get('tax_scheme_name', 'No aplica'))

    # --- Attachment principal: XML firmado de la factura ---
    attachment = _add(root, _cac('Attachment'))
    ext_ref = _add(attachment, _cac('ExternalReference'))
    _add(ext_ref, _cbc('MimeCode'), 'text/xml')
    _add(ext_ref, _cbc('EncodingCode'), 'UTF-8')
    desc_el = etree.SubElement(ext_ref, _cbc('Description'))
    desc_el.text = etree.CDATA(invoice.signed_xml or '')

    # --- ParentDocumentLineReference con ApplicationResponse ---
    pdlr = _add(root, _cac('ParentDocumentLineReference'))
    _add(pdlr, _cbc('LineID'), '1')

    doc_ref = _add(pdlr, _cac('DocumentReference'))
    _add(doc_ref, _cbc('ID'), invoice.full_number)
    _add(doc_ref, _cbc('UUID'), invoice.cufe or '', attrib={'schemeName': 'CUFE-SHA384'})
    _add(doc_ref, _cbc('IssueDate'), str(invoice.invoice_date))
    _add(doc_ref, _cbc('DocumentType'), 'ApplicationResponse')

    if application_response_xml:
        ar_attachment = _add(doc_ref, _cac('Attachment'))
        ar_ext_ref = _add(ar_attachment, _cac('ExternalReference'))
        _add(ar_ext_ref, _cbc('MimeCode'), 'text/xml')
        _add(ar_ext_ref, _cbc('EncodingCode'), 'UTF-8')
        ar_desc = etree.SubElement(ar_ext_ref, _cbc('Description'))
        ar_desc.text = etree.CDATA(application_response_xml)

    # ResultOfVerification
    rov = _add(doc_ref, _cac('ResultOfVerification'))
    _add(rov, _cbc('ValidatorID'),
         'Unidad Especial Dirección de Impuestos y Aduanas Nacionales')
    _add(rov, _cbc('ValidationResultCode'), '02')

    if invoice.processed_at:
        proc_bogota = invoice.processed_at.astimezone(BOGOTA_TZ)
        _add(rov, _cbc('ValidationDate'), proc_bogota.strftime('%Y-%m-%d'))
        _add(rov, _cbc('ValidationTime'), proc_bogota.strftime('%H:%M:%S') + '-05:00')
    else:
        _add(rov, _cbc('ValidationDate'), str(invoice.invoice_date))
        _add(rov, _cbc('ValidationTime'), '00:00:00-05:00')

    xml_bytes = etree.tostring(
        root, xml_declaration=True, encoding='UTF-8', pretty_print=True
    )
    return xml_bytes.decode('utf-8')
