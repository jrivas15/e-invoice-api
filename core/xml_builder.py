"""
Build DIAN UBL 2.1 invoice XML for Colombia.

Returns (xml_str, cufe_str) — no file I/O, no ORM calls.
"""
import hashlib
from collections import defaultdict
from datetime import datetime, time as dt_time
from decimal import Decimal, ROUND_HALF_UP

from lxml import etree
import pytz

BOGOTA_TZ = pytz.timezone('America/Bogota')

# Namespaces
NS_INVOICE = 'urn:oasis:names:specification:ubl:schema:xsd:Invoice-2'
NS_CAC = 'urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2'
NS_CBC = 'urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2'
NS_EXT = 'urn:oasis:names:specification:ubl:schema:xsd:CommonExtensionComponents-2'
NS_STS = 'http://www.dian.gov.co/contratos/facturaelectronica/v1/Structures'
NS_XADES = 'http://uri.etsi.org/01903/v1.3.2#'
NS_XADES141 = 'http://uri.etsi.org/01903/v1.4.1#'
NS_DS = 'http://www.w3.org/2000/09/xmldsig#'
NS_XSI = 'http://www.w3.org/2001/XMLSchema-instance'

NSMAP = {
    None:      NS_INVOICE,
    'cac':     NS_CAC,
    'cbc':     NS_CBC,
    'ext':     NS_EXT,
    'sts':     NS_STS,
    'xades':   NS_XADES,
    'xades141': NS_XADES141,
    'ds':      NS_DS,
    'xsi':     NS_XSI,
}

# Namespace helpers
def _cbc(tag): return f'{{{NS_CBC}}}{tag}'
def _cac(tag): return f'{{{NS_CAC}}}{tag}'
def _ext(tag): return f'{{{NS_EXT}}}{tag}'
def _sts(tag): return f'{{{NS_STS}}}{tag}'

# DIAN NIT (authorization provider)
DIAN_NIT = '800197268'


def _dec(value) -> Decimal:
    return Decimal(str(value)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def build_xml(invoice, config) -> tuple[str, str]:
    """
    Build DIAN UBL 2.1 XML for a sales invoice (type 01).

    Parameters
    ----------
    invoice : Invoice model instance
    config  : FiscalConfig model instance

    Returns
    -------
    (xml_str, cufe_str)
    """
    profile_execution_id = '2' if config.ambiente == 'PRUEBAS' else '1'

    # --- Issue date/time ---------------------------------------------------
    if invoice.invoice_date:
        if isinstance(invoice.invoice_date, datetime):
            issue_dt = invoice.invoice_date
            if issue_dt.tzinfo is None:
                issue_dt = BOGOTA_TZ.localize(issue_dt)
        else:
            issue_dt = BOGOTA_TZ.localize(
                datetime.combine(invoice.invoice_date, dt_time(0, 0, 0))
            )
    else:
        issue_dt = datetime.now(BOGOTA_TZ)

    issue_date = issue_dt.strftime('%Y-%m-%d')
    issue_time = issue_dt.strftime('%H:%M:%S') + '-05:00'

    receiver = invoice.customer or {}
    items    = invoice.items or []
    currency = invoice.currency or 'COP'

    # --- Totals computed from items (source of truth for XML consistency) ---
    # FAU02 requires LegalMonetaryTotal/LineExtensionAmount == sum of line amounts
    # FAU04 requires TaxTotal/TaxableAmount == sum of line TaxableAmounts
    computed_line_extension = Decimal('0.00')
    computed_iva             = Decimal('0.00')

    for _item in items:
        _qty      = Decimal(str(_item.get('quantity', 1)))
        _price    = _dec(_item.get('unit_price', 0))
        _disc     = _dec(_item.get('discount', 0))
        _taxable  = ((_price - _disc) * _qty).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        computed_line_extension += _taxable
        for _tax in _item.get('taxes', []):
            _rate = _dec(_tax.get('rate', 0))
            computed_iva += (_taxable * _rate / 100).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

    computed_line_extension = computed_line_extension.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    computed_iva            = computed_iva.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    computed_total          = (computed_line_extension + computed_iva).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

    # --- Security codes -----------------------------------------------------
    software_security_code = hashlib.sha384(
        f"{config.software_id}{config.software_pin}{invoice.full_number}".encode()
    ).hexdigest()

    cufe_raw = (
        f"{invoice.full_number}"
        f"{issue_date}"
        f"{issue_time}"
        f"{computed_line_extension:.2f}"
        f"01{computed_iva:.2f}"
        f"040.00"
        f"030.00"
        f"{computed_total:.2f}"
        f"{config.nit}"
        f"{receiver.get('document_number', '')}"
        f"{config.clave_tecnica}"
        f"{profile_execution_id}"
    )
    cufe = hashlib.sha384(cufe_raw.encode()).hexdigest()

    # --- QR -----------------------------------------------------------------
    if config.ambiente == 'PRUEBAS':
        find_url = f'https://catalogo-vpfe-hab.dian.gov.co/Document/FindDocument?documentKey={cufe}'
    else:
        find_url = f'https://catalogo-vpfe.dian.gov.co/Document/FindDocument?documentKey={cufe}'

    qr_content = (
        f"NroFactura={invoice.full_number}"
        f" FechaFactura={issue_date}"
        f" HorFac={issue_time}"
        f" NitFacturador={config.nit}"
        f" NitAdquiriente={receiver.get('document_number', '')}"
        f" ValorTotalFactura={float(computed_line_extension)}"
        f" ValIva={float(computed_iva)}"
        f" ValOtroIm=0.0"
        f" ValTolFac={float(computed_total)}"
        f" CUFE={cufe}"
        f" URL={find_url}"
    )

    # --- XML root -----------------------------------------------------------
    root = etree.Element(f'{{{NS_INVOICE}}}Invoice', nsmap=NSMAP)
    root.set(
        f'{{{NS_XSI}}}schemaLocation',
        'urn:oasis:names:specification:ubl:schema:xsd:Invoice-2 '
        'http://docs.oasis-open.org/ubl/os-UBL-2.1/xsd/maindoc/UBL-Invoice-2.1.xsd',
    )

    # Extensions block
    ubl_exts = etree.SubElement(root, _ext('UBLExtensions'))

    # Extension 1: DianExtensions
    ubl_ext1 = etree.SubElement(ubl_exts, _ext('UBLExtension'))
    ext_content1 = etree.SubElement(ubl_ext1, _ext('ExtensionContent'))
    _build_dian_extensions(ext_content1, config, software_security_code, qr_content)

    # Extension 2: placeholder for XAdES signature (signer fills this)
    ubl_ext2 = etree.SubElement(ubl_exts, _ext('UBLExtension'))
    etree.SubElement(ubl_ext2, _ext('ExtensionContent'))

    # --- Header fields ------------------------------------------------------
    etree.SubElement(root, _cbc('UBLVersionID')).text = 'UBL 2.1'
    etree.SubElement(root, _cbc('CustomizationID')).text = '10'
    etree.SubElement(root, _cbc('ProfileID')).text = 'DIAN 2.1: Factura Electrónica de Venta'
    etree.SubElement(root, _cbc('ProfileExecutionID')).text = profile_execution_id

    etree.SubElement(root, _cbc('ID')).text = invoice.full_number

    uuid_el = etree.SubElement(root, _cbc('UUID'))
    uuid_el.set('schemeID', profile_execution_id)
    uuid_el.set('schemeName', 'CUFE-SHA384')
    uuid_el.text = cufe

    etree.SubElement(root, _cbc('IssueDate')).text = issue_date
    etree.SubElement(root, _cbc('IssueTime')).text = issue_time

    etree.SubElement(root, _cbc('InvoiceTypeCode')).text = '01'

    doc_currency = etree.SubElement(root, _cbc('DocumentCurrencyCode'))
    doc_currency.set('listAgencyID', '6')
    doc_currency.set('listAgencyName',
                     'United Nations Economic Commission for Europe')
    doc_currency.set('listID', 'ISO 4217 Alpha')
    doc_currency.text = currency

    etree.SubElement(root, _cbc('LineCountNumeric')).text = str(len(items))

    # --- Invoice period (authorization dates) --------------------------------
    inv_period = etree.SubElement(root, _cac('InvoicePeriod'))
    etree.SubElement(inv_period, _cbc('StartDate')).text = str(config.resolution_date)
    etree.SubElement(inv_period, _cbc('EndDate')).text = (
        str(config.resolution_end_date) if config.resolution_end_date else ''
    )

    # --- Issuer (AccountingSupplierParty) -----------------------------------
    _build_supplier_party(root, config)

    # --- Receiver (AccountingCustomerParty) ---------------------------------
    _build_customer_party(root, receiver)

    # --- Payment means ------------------------------------------------------
    pm = etree.SubElement(root, _cac('PaymentMeans'))
    etree.SubElement(pm, _cbc('ID')).text = '1'
    etree.SubElement(pm, _cbc('PaymentMeansCode')).text = str(invoice.payment_means_code)

    # --- Tax total ----------------------------------------------------------
    _build_tax_total(root, items, currency)

    # --- Legal monetary total -----------------------------------------------
    lmt = etree.SubElement(root, _cac('LegalMonetaryTotal'))
    _amt(lmt, _cbc('LineExtensionAmount'), computed_line_extension, currency)
    _amt(lmt, _cbc('TaxExclusiveAmount'), computed_line_extension, currency)
    _amt(lmt, _cbc('TaxInclusiveAmount'), computed_total, currency)
    _amt(lmt, _cbc('ChargeTotalAmount'), Decimal('0.00'), currency)
    _amt(lmt, _cbc('PayableAmount'), computed_total, currency)

    # --- Invoice lines ------------------------------------------------------
    for i, item in enumerate(items, start=1):
        _build_invoice_line(root, item, i, currency)

    xml_str = etree.tostring(
        root,
        xml_declaration=True,
        encoding='UTF-8',
        pretty_print=True,
    ).decode('utf-8')

    return xml_str, cufe


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _amt(parent, tag, value: Decimal, currency: str):
    el = etree.SubElement(parent, tag)
    el.set('currencyID', currency)
    el.text = f'{value:.2f}'
    return el


def _build_dian_extensions(parent, config, software_security_code: str, qr_content: str):
    dian_ext = etree.SubElement(parent, _sts('DianExtensions'))

    # InvoiceControl
    inv_ctrl = etree.SubElement(dian_ext, _sts('InvoiceControl'))
    etree.SubElement(inv_ctrl, _sts('InvoiceAuthorization')).text = config.resolution_number
    auth_period = etree.SubElement(inv_ctrl, _sts('AuthorizationPeriod'))
    etree.SubElement(auth_period, _cbc('StartDate')).text = str(config.resolution_date)
    etree.SubElement(auth_period, _cbc('EndDate')).text = (
        str(config.resolution_end_date) if config.resolution_end_date else ''
    )
    auth_invs = etree.SubElement(inv_ctrl, _sts('AuthorizedInvoices'))
    etree.SubElement(auth_invs, _sts('Prefix')).text = config.invoice_prefix or ''
    etree.SubElement(auth_invs, _sts('From')).text = str(config.range_start)
    etree.SubElement(auth_invs, _sts('To')).text = str(config.range_end)

    # InvoiceSource
    inv_src = etree.SubElement(dian_ext, _sts('InvoiceSource'))
    id_code = etree.SubElement(inv_src, _cbc('IdentificationCode'))
    id_code.set('listAgencyID', '6')
    id_code.set('listAgencyName',
                'United Nations Economic Commission for Europe')
    id_code.set('listSchemeURI',
                'urn:oasis:names:specification:ubl:codelist:gc:CountryIdentificationCode-2.1')
    id_code.text = 'CO'

    # SoftwareProvider
    sw_prov = etree.SubElement(dian_ext, _sts('SoftwareProvider'))
    prov_id = etree.SubElement(sw_prov, _sts('ProviderID'))
    prov_id.set('schemeAgencyID', '195')
    prov_id.set('schemeAgencyName',
                'CO, DIAN (Dirección de Impuestos y Aduanas Nacionales)')
    prov_id.set('schemeID', config.check_digit)
    prov_id.set('schemeName', '31')
    prov_id.text = config.nit
    sw_id_el = etree.SubElement(sw_prov, _sts('SoftwareID'))
    sw_id_el.set('schemeAgencyID', '195')
    sw_id_el.set('schemeAgencyName',
                 'CO, DIAN (Dirección de Impuestos y Aduanas Nacionales)')
    sw_id_el.text = config.software_id

    # SoftwareSecurityCode
    sw_sec = etree.SubElement(dian_ext, _sts('SoftwareSecurityCode'))
    sw_sec.set('schemeAgencyID', '195')
    sw_sec.set('schemeAgencyName',
               'CO, DIAN (Dirección de Impuestos y Aduanas Nacionales)')
    sw_sec.text = software_security_code

    # AuthorizationProvider
    auth_prov = etree.SubElement(dian_ext, _sts('AuthorizationProvider'))
    auth_prov_id = etree.SubElement(auth_prov, _sts('AuthorizationProviderID'))
    auth_prov_id.set('schemeAgencyID', '195')
    auth_prov_id.set('schemeAgencyName',
                     'CO, DIAN (Dirección de Impuestos y Aduanas Nacionales)')
    auth_prov_id.set('schemeID', '4')
    auth_prov_id.set('schemeName', '31')
    auth_prov_id.text = DIAN_NIT

    etree.SubElement(dian_ext, _sts('QRCode')).text = qr_content


def _build_supplier_party(root, config):
    sp = etree.SubElement(root, _cac('AccountingSupplierParty'))
    etree.SubElement(sp, _cbc('AdditionalAccountID')).text = config.person_type
    party = etree.SubElement(sp, _cac('Party'))

    pn = etree.SubElement(party, _cac('PartyName'))
    etree.SubElement(pn, _cbc('Name')).text = config.trade_name or config.legal_name

    loc = etree.SubElement(party, _cac('PhysicalLocation'))
    addr = etree.SubElement(loc, _cac('Address'))
    etree.SubElement(addr, _cbc('ID')).text = config.fiscal_city_code or config.city_code
    etree.SubElement(addr, _cbc('CityName')).text = config.fiscal_city_name or config.city_name
    etree.SubElement(addr, _cbc('CountrySubentity')).text = (
        config.fiscal_department or ''
    )
    etree.SubElement(addr, _cbc('CountrySubentityCode')).text = (
        config.fiscal_department_code or config.department_code
    )
    al = etree.SubElement(addr, _cac('AddressLine'))
    etree.SubElement(al, _cbc('Line')).text = config.fiscal_address or config.address
    country = etree.SubElement(addr, _cac('Country'))
    cc = etree.SubElement(country, _cbc('IdentificationCode'))
    cc.text = config.country_code
    cn = etree.SubElement(country, _cbc('Name'))
    cn.set('languageID', 'es')
    cn.text = 'Colombia'

    pts = etree.SubElement(party, _cac('PartyTaxScheme'))
    etree.SubElement(pts, _cbc('RegistrationName')).text = config.legal_name
    cid = etree.SubElement(pts, _cbc('CompanyID'))
    cid.set('schemeAgencyID', '195')
    cid.set('schemeAgencyName',
            'CO, DIAN (Dirección de Impuestos y Aduanas Nacionales)')
    cid.set('schemeID', config.check_digit)
    cid.set('schemeName', '31')
    cid.text = config.nit
    tlc = etree.SubElement(pts, _cbc('TaxLevelCode'))
    tax_level = ';'.join(config.tax_responsibilities) if config.tax_responsibilities else 'R-99-PN'
    tlc.set('listName', '')
    tlc.text = tax_level

    # RegistrationAddress inside PartyTaxScheme (required by DIAN)
    reg_addr = etree.SubElement(pts, _cac('RegistrationAddress'))
    etree.SubElement(reg_addr, _cbc('ID')).text = config.fiscal_city_code or config.city_code
    etree.SubElement(reg_addr, _cbc('CityName')).text = config.fiscal_city_name or config.city_name
    etree.SubElement(reg_addr, _cbc('CountrySubentity')).text = (
        config.fiscal_department or ''
    )
    etree.SubElement(reg_addr, _cbc('CountrySubentityCode')).text = (
        config.fiscal_department_code or config.department_code
    )
    ral = etree.SubElement(reg_addr, _cac('AddressLine'))
    etree.SubElement(ral, _cbc('Line')).text = config.fiscal_address or config.address
    reg_country = etree.SubElement(reg_addr, _cac('Country'))
    rcc = etree.SubElement(reg_country, _cbc('IdentificationCode'))
    rcc.text = config.country_code
    rcn = etree.SubElement(reg_country, _cbc('Name'))
    rcn.set('languageID', 'es')
    rcn.text = 'Colombia'

    ts = etree.SubElement(pts, _cac('TaxScheme'))
    etree.SubElement(ts, _cbc('ID')).text = config.tax_scheme_id
    etree.SubElement(ts, _cbc('Name')).text = config.tax_scheme_name

    ple = etree.SubElement(party, _cac('PartyLegalEntity'))
    etree.SubElement(ple, _cbc('RegistrationName')).text = config.legal_name
    legal_cid = etree.SubElement(ple, _cbc('CompanyID'))
    legal_cid.set('schemeAgencyID', '195')
    legal_cid.set('schemeAgencyName',
                  'CO, DIAN (Dirección de Impuestos y Aduanas Nacionales)')
    legal_cid.set('schemeID', config.check_digit)
    legal_cid.set('schemeName', '31')
    legal_cid.text = config.nit
    # CorporateRegistrationScheme — invoice prefix
    if config.invoice_prefix:
        crs = etree.SubElement(ple, _cac('CorporateRegistrationScheme'))
        etree.SubElement(crs, _cbc('ID')).text = config.invoice_prefix

    contact = etree.SubElement(party, _cac('Contact'))
    etree.SubElement(contact, _cbc('ElectronicMail')).text = config.email


def _build_customer_party(root, receiver: dict):
    cp = etree.SubElement(root, _cac('AccountingCustomerParty'))
    etree.SubElement(cp, _cbc('AdditionalAccountID')).text = receiver.get('person_type', '2')
    party = etree.SubElement(cp, _cac('Party'))

    # PartyIdentification — required for receiver
    pi = etree.SubElement(party, _cac('PartyIdentification'))
    pi_id = etree.SubElement(pi, _cbc('ID'))
    pi_id.set('schemeName', receiver.get('document_type', '31'))
    pi_id.text = receiver.get('document_number', '')

    pn = etree.SubElement(party, _cac('PartyName'))
    etree.SubElement(pn, _cbc('Name')).text = receiver.get('legal_name', '')

    # Minimal PhysicalLocation for customer
    loc = etree.SubElement(party, _cac('PhysicalLocation'))
    addr = etree.SubElement(loc, _cac('Address'))
    al = etree.SubElement(addr, _cac('AddressLine'))
    etree.SubElement(al, _cbc('Line')).text = receiver.get('address', '')
    country = etree.SubElement(addr, _cac('Country'))
    cc = etree.SubElement(country, _cbc('IdentificationCode'))
    cc.text = 'CO'
    cn = etree.SubElement(country, _cbc('Name'))
    cn.set('languageID', 'es')
    cn.text = 'Colombia'

    doc_type = receiver.get('document_type', '13')
    dv = receiver.get('dv', '')

    pts = etree.SubElement(party, _cac('PartyTaxScheme'))
    etree.SubElement(pts, _cbc('RegistrationName')).text = receiver.get('legal_name', '')
    cid = etree.SubElement(pts, _cbc('CompanyID'))
    cid.set('schemeAgencyID', '195')
    cid.set('schemeAgencyName',
            'CO, DIAN (Dirección de Impuestos y Aduanas Nacionales)')
    cid.set('schemeID', dv)
    cid.set('schemeName', doc_type)
    cid.text = receiver.get('document_number', '')
    tlc = etree.SubElement(pts, _cbc('TaxLevelCode'))
    tlc.text = receiver.get('tax_level_code', 'R-99-PN')
    ts = etree.SubElement(pts, _cac('TaxScheme'))
    etree.SubElement(ts, _cbc('ID')).text = receiver.get('tax_scheme_id', 'ZZ')
    etree.SubElement(ts, _cbc('Name')).text = receiver.get('tax_scheme_name', 'No aplica')

    ple = etree.SubElement(party, _cac('PartyLegalEntity'))
    etree.SubElement(ple, _cbc('RegistrationName')).text = receiver.get('legal_name', '')
    legal_cid = etree.SubElement(ple, _cbc('CompanyID'))
    legal_cid.set('schemeAgencyID', '195')
    legal_cid.set('schemeAgencyName',
                  'CO, DIAN (Dirección de Impuestos y Aduanas Nacionales)')
    legal_cid.set('schemeID', dv)
    legal_cid.set('schemeName', doc_type)
    legal_cid.text = receiver.get('document_number', '')

    contact = etree.SubElement(party, _cac('Contact'))
    etree.SubElement(contact, _cbc('Telephone')).text = receiver.get('phone', '')
    etree.SubElement(contact, _cbc('ElectronicMail')).text = receiver.get('email', '')


_TAX_TYPE_NAMES = {'01': 'IVA', '03': 'ICA', '04': 'INC'}


def _build_tax_total(root, items: list, currency: str):
    """
    Build one cac:TaxTotal per tax type found across all items.
    Within each TaxTotal, one cac:TaxSubtotal per distinct rate.
    Only tax types actually present in items are emitted.
    """
    # {tax_type: {tax_rate: {'taxable': Decimal, 'tax': Decimal}}}
    tax_data: dict = defaultdict(lambda: defaultdict(
        lambda: {'taxable': Decimal('0.00'), 'tax': Decimal('0.00')}
    ))

    for item in items:
        item_qty = Decimal(str(item.get('quantity', 1)))
        unit_price = _dec(item.get('unit_price', 0))
        item_discount = _dec(item.get('discount', 0))

        taxable = ((unit_price - item_discount) * item_qty).quantize(
            Decimal('0.01'), rounding=ROUND_HALF_UP
        )

        for tax in item.get('taxes', []):
            tax_type = tax.get('type', '01')
            tax_rate = _dec(tax.get('rate', 0))
            item_tax = (taxable * tax_rate / 100).quantize(
                Decimal('0.01'), rounding=ROUND_HALF_UP
            )
            tax_data[tax_type][tax_rate]['taxable'] += taxable
            tax_data[tax_type][tax_rate]['tax'] += item_tax

    for tax_type, subtotals in tax_data.items():
        total_tax = sum(v['tax'] for v in subtotals.values()).quantize(
            Decimal('0.01'), rounding=ROUND_HALF_UP
        )

        tt = etree.SubElement(root, _cac('TaxTotal'))
        _amt(tt, _cbc('TaxAmount'), total_tax, currency)

        for tax_rate, amounts in subtotals.items():
            tsub = etree.SubElement(tt, _cac('TaxSubtotal'))
            _amt(tsub, _cbc('TaxableAmount'),
                 amounts['taxable'].quantize(Decimal('0.01'), rounding=ROUND_HALF_UP),
                 currency)
            _amt(tsub, _cbc('TaxAmount'),
                 amounts['tax'].quantize(Decimal('0.01'), rounding=ROUND_HALF_UP),
                 currency)
            tc = etree.SubElement(tsub, _cac('TaxCategory'))
            etree.SubElement(tc, _cbc('Percent')).text = f'{tax_rate:.2f}'
            ts = etree.SubElement(tc, _cac('TaxScheme'))
            etree.SubElement(ts, _cbc('ID')).text = tax_type
            etree.SubElement(ts, _cbc('Name')).text = _TAX_TYPE_NAMES.get(tax_type, tax_type)


def _build_invoice_line(root, item: dict, line_num: int, currency: str):
    item_qty      = Decimal(str(item.get('quantity', 1)))
    unit_price    = _dec(item.get('unit_price', 0))
    item_discount = _dec(item.get('discount', 0))
    unit_code     = item.get('unit_code', '94')

    # First tax entry drives the line-level TaxTotal (DIAN requires one per line)
    first_tax  = item.get('taxes', [{}])[0]
    tax_rate   = _dec(first_tax.get('rate', 0))
    tax_type   = first_tax.get('type', '01')

    total_sin_imp = ((unit_price - item_discount) * item_qty).quantize(
        Decimal('0.01'), rounding=ROUND_HALF_UP
    )
    item_tax = (total_sin_imp * tax_rate / 100).quantize(
        Decimal('0.01'), rounding=ROUND_HALF_UP
    )

    line = etree.SubElement(root, _cac('InvoiceLine'))
    etree.SubElement(line, _cbc('ID')).text = str(item.get('id', line_num))

    iq = etree.SubElement(line, _cbc('InvoicedQuantity'))
    iq.set('unitCode', unit_code)
    iq.text = str(int(item_qty))

    _amt(line, _cbc('LineExtensionAmount'), total_sin_imp, currency)

    # AllowanceCharge only when there is an actual discount (FBE08)
    if item_discount > 0:
        discount_amt = (item_discount * item_qty).quantize(
            Decimal('0.01'), rounding=ROUND_HALF_UP
        )
        gross_amt = (unit_price * item_qty).quantize(
            Decimal('0.01'), rounding=ROUND_HALF_UP
        )
        multiplier = (item_discount / unit_price * 100).quantize(
            Decimal('0.01'), rounding=ROUND_HALF_UP
        )
        ac = etree.SubElement(line, _cac('AllowanceCharge'))
        etree.SubElement(ac, _cbc('ID')).text = str(line_num)
        etree.SubElement(ac, _cbc('ChargeIndicator')).text = 'false'
        etree.SubElement(ac, _cbc('MultiplierFactorNumeric')).text = f'{multiplier:.2f}'
        _amt(ac, _cbc('Amount'), discount_amt, currency)
        _amt(ac, _cbc('BaseAmount'), gross_amt, currency)

    ltt = etree.SubElement(line, _cac('TaxTotal'))
    _amt(ltt, _cbc('TaxAmount'), item_tax, currency)
    ltsub = etree.SubElement(ltt, _cac('TaxSubtotal'))
    _amt(ltsub, _cbc('TaxableAmount'), total_sin_imp, currency)
    _amt(ltsub, _cbc('TaxAmount'), item_tax, currency)
    ltc = etree.SubElement(ltsub, _cac('TaxCategory'))
    etree.SubElement(ltc, _cbc('Percent')).text = f'{tax_rate:.2f}'
    lts = etree.SubElement(ltc, _cac('TaxScheme'))
    etree.SubElement(lts, _cbc('ID')).text = tax_type
    etree.SubElement(lts, _cbc('Name')).text = _TAX_TYPE_NAMES.get(tax_type, tax_type)

    item_el = etree.SubElement(line, _cac('Item'))
    etree.SubElement(item_el, _cbc('Description')).text = item.get('description', '')
    std = etree.SubElement(item_el, _cac('StandardItemIdentification'))
    std_id = etree.SubElement(std, _cbc('ID'))
    std_id.set('schemeAgencyID', '')
    std_id.set('schemeID', '999')
    std_id.set('schemeName', '')
    std_id.text = str(item.get('id', line_num))

    price_el = etree.SubElement(line, _cac('Price'))
    _amt(price_el, _cbc('PriceAmount'), unit_price, currency)
    bq = etree.SubElement(price_el, _cbc('BaseQuantity'))
    bq.set('unitCode', unit_code)
    bq.text = str(int(item_qty))
