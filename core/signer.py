"""
XAdES-BES XML digital signature for DIAN Colombia.

sign_xml(xml_str, p12_bytes, password) -> signed_xml_str
No file I/O — everything in memory.
"""
import base64
import hashlib

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.serialization import pkcs12
from lxml import etree

# Namespaces
NS_INVOICE = 'urn:oasis:names:specification:ubl:schema:xsd:Invoice-2'
NS_CAC     = 'urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2'
NS_CBC     = 'urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2'
NS_DS      = 'http://www.w3.org/2000/09/xmldsig#'
NS_EXT     = 'urn:oasis:names:specification:ubl:schema:xsd:CommonExtensionComponents-2'
NS_STS     = 'http://www.dian.gov.co/contratos/facturaelectronica/v1/Structures'
NS_XADES   = 'http://uri.etsi.org/01903/v1.3.2#'
NS_XADES141 = 'http://uri.etsi.org/01903/v1.4.1#'
NS_XSI     = 'http://www.w3.org/2001/XMLSchema-instance'

# All namespaces inherited from the Invoice root element (used for C14N simulation)
INVOICE_SCHEMAS = (
    f'xmlns="{NS_INVOICE}" '
    f'xmlns:cac="{NS_CAC}" '
    f'xmlns:cbc="{NS_CBC}" '
    f'xmlns:ds="{NS_DS}" '
    f'xmlns:ext="{NS_EXT}" '
    f'xmlns:sts="{NS_STS}" '
    f'xmlns:xades="{NS_XADES}" '
    f'xmlns:xades141="{NS_XADES141}" '
    f'xmlns:xsi="{NS_XSI}"'
)

# DIAN signature policy v2
POLICY_URL  = 'https://facturaelectronica.dian.gov.co/politicadefirma/v2/politicadefirmav2.pdf'
POLICY_DESC = 'Política de firma para facturas electrónicas de la República de Colombia'
POLICY_HASH = 'dMoMvtcG5aIzgYo0tIsSQeVJBDnUnfSOfBpxXrmor0Y='

def sign_xml(xml_str: str, p12_bytes: bytes, password: str) -> str:
    """
    Sign a DIAN UBL 2.1 invoice XML with XAdES-BES.

    Parameters
    ----------
    xml_str   : Unsigned XML string (UTF-8)
    p12_bytes : PKCS#12 (.p12/.pfx) certificate bytes
    password  : Certificate password

    Returns
    -------
    Signed XML string
    """
    # --- Load certificate ---------------------------------------------------
    pfx         = pkcs12.load_pkcs12(p12_bytes, password.encode())
    private_key = pfx.key
    cert        = pfx.cert.certificate

    # cert_b64: PEM content without header/footer/newlines (base64 of DER)
    pem = cert.public_bytes(serialization.Encoding.PEM).decode()
    cert_b64 = (
        pem
        .replace('-----BEGIN CERTIFICATE-----', '')
        .replace('-----END CERTIFICATE-----', '')
        .replace('\n', '')
        .replace('\r', '')
    )
    # CertDigest per DIAN convention: SHA256 of the base64 string bytes
    cert_digest = base64.b64encode(
        hashlib.sha256(cert_b64.encode('ascii')).digest()
    ).decode()

    issuer_name = _build_issuer_name(cert)
    serial_num  = str(cert.serial_number)

    # --- IDs (use CUFE from XML so they match the document) ----------------
    root        = etree.fromstring(xml_str.encode('utf-8'))
    cufe        = _extract_cufe(root)
    sig_id      = f'xmldsig-{cufe}'
    ref0_id     = f'{sig_id}-ref0'
    key_info_id = f'{sig_id}-KeyInfo'
    sp_id       = f'{sig_id}-signedprops'
    sig_val_id  = f'{sig_id}-sigvalue'

    # --- Signing time = IssueDate + IssueTime from the document ------------
    signing_time = _extract_signing_time(root)

    # --- Reference 1: document digest (whole doc, enveloped-sig transform) --
    doc_c14n   = etree.tostring(root, method='c14n')
    doc_digest = _sha256b64(doc_c14n)

    # --- Reference 2: KeyInfo digest ----------------------------------------
    key_info_str = (
        f'<ds:KeyInfo Id="{key_info_id}">'
        f'<ds:X509Data>'
        f'<ds:X509Certificate>{cert_b64}</ds:X509Certificate>'
        f'</ds:X509Data>'
        f'</ds:KeyInfo>'
    )
    key_info_with_schemas = key_info_str.replace(
        '<ds:KeyInfo', f'<ds:KeyInfo {INVOICE_SCHEMAS}'
    )
    key_info_digest = _sha256b64(key_info_with_schemas.encode('utf-8'))

    # --- Reference 3: SignedProperties digest --------------------------------
    sp_str = _build_signed_properties(
        sp_id, signing_time, cert_digest, issuer_name, serial_num
    )
    sp_with_schemas = sp_str.replace(
        '<xades:SignedProperties', f'<xades:SignedProperties {INVOICE_SCHEMAS}'
    )
    sp_digest = _sha256b64(sp_with_schemas.encode('utf-8'))

    # --- SignedInfo ----------------------------------------------------------
    signed_info_str = _build_signed_info(
        ref0_id, doc_digest,
        key_info_id, key_info_digest,
        sp_id, sp_digest,
    )
    # Add inherited namespaces before signing (simulates C14N in document context)
    signed_info_for_signing = signed_info_str.replace(
        '<ds:SignedInfo', f'<ds:SignedInfo {INVOICE_SCHEMAS}'
    )
    sig_bytes  = private_key.sign(
        signed_info_for_signing.encode('utf-8'),
        padding.PKCS1v15(),
        hashes.SHA256(),
    )
    sig_value  = base64.b64encode(sig_bytes).decode()

    # --- Assemble Signature element -----------------------------------------
    signature_xml = (
        f'<ds:Signature xmlns:ds="{NS_DS}" Id="{sig_id}">'
        f'{signed_info_str}'
        f'<ds:SignatureValue Id="{sig_val_id}">{sig_value}</ds:SignatureValue>'
        f'{key_info_str}'
        f'<ds:Object>'
        f'<xades:QualifyingProperties xmlns:xades="{NS_XADES}" Target="#{sig_id}">'
        f'{sp_str}'
        f'</xades:QualifyingProperties>'
        f'</ds:Object>'
        f'</ds:Signature>'
    )

    # --- Insert into the second ExtensionContent placeholder ---------------
    sig_elem = etree.fromstring(signature_xml.encode('utf-8'))
    _insert_signature(root, sig_elem)

    return etree.tostring(
        root,
        xml_declaration=True,
        encoding='UTF-8',
        pretty_print=True,
    ).decode('utf-8')


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _sha256b64(data: bytes) -> str:
    return base64.b64encode(hashlib.sha256(data).digest()).decode()


def _extract_cufe(root) -> str:
    """Extract CUFE from cbc:UUID element."""
    ns = 'urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2'
    el = root.find(f'{{{ns}}}UUID')
    return el.text if el is not None else 'unknown'


def _extract_signing_time(root) -> str:
    """
    Build signing time from IssueDate + IssueTime in the document.
    IssueTime already contains the offset (e.g. '22:22:56-05:00'),
    so the result is 'YYYY-MM-DDTHH:MM:SS-05:00'.
    """
    ns = 'urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2'
    date_el = root.find(f'{{{ns}}}IssueDate')
    time_el = root.find(f'{{{ns}}}IssueTime')
    issue_date = date_el.text if date_el is not None else ''
    issue_time = time_el.text if time_el is not None else '00:00:00-05:00'
    # IssueTime format: HH:MM:SS-05:00  →  strip offset, keep just HH:MM:SS-05:00
    return f'{issue_date}T{issue_time}'


def _build_issuer_name(cert) -> str:
    """
    Build issuer DN string matching DIAN format:
    forward order (C first, CN last), using dotted OID for non-standard attributes.
    """
    from cryptography.x509.oid import NameOID
    OID_MAP = {
        NameOID.COMMON_NAME.dotted_string:              'CN',
        NameOID.ORGANIZATION_NAME.dotted_string:        'O',
        NameOID.ORGANIZATIONAL_UNIT_NAME.dotted_string: 'OU',
        NameOID.COUNTRY_NAME.dotted_string:             'C',
        NameOID.STATE_OR_PROVINCE_NAME.dotted_string:   'ST',
        NameOID.LOCALITY_NAME.dotted_string:            'L',
        NameOID.EMAIL_ADDRESS.dotted_string:            'E',
        # SERIAL_NUMBER (2.5.4.5) kept as dotted OID to match DIAN format
    }
    parts = []
    for attr in cert.issuer:
        short = OID_MAP.get(attr.oid.dotted_string, attr.oid.dotted_string)
        parts.append(f'{short}={attr.value}')
    return ','.join(parts)


def _build_signed_properties(sp_id, signing_time, cert_digest, issuer_name, serial_num) -> str:
    return (
        f'<xades:SignedProperties Id="{sp_id}">'
        f'<xades:SignedSignatureProperties>'
        f'<xades:SigningTime>{signing_time}</xades:SigningTime>'
        f'<xades:SigningCertificate>'
        f'<xades:Cert>'
        f'<xades:CertDigest>'
        f'<ds:DigestMethod Algorithm="http://www.w3.org/2001/04/xmlenc#sha256"></ds:DigestMethod>'
        f'<ds:DigestValue>{cert_digest}</ds:DigestValue>'
        f'</xades:CertDigest>'
        f'<xades:IssuerSerial>'
        f'<ds:X509IssuerName>{issuer_name}</ds:X509IssuerName>'
        f'<ds:X509SerialNumber>{serial_num}</ds:X509SerialNumber>'
        f'</xades:IssuerSerial>'
        f'</xades:Cert>'
        f'</xades:SigningCertificate>'
        f'<xades:SignaturePolicyIdentifier>'
        f'<xades:SignaturePolicyId>'
        f'<xades:SigPolicyId>'
        f'<xades:Identifier>{POLICY_URL}</xades:Identifier>'
        f'<xades:Description>{POLICY_DESC}</xades:Description>'
        f'</xades:SigPolicyId>'
        f'<xades:SigPolicyHash>'
        f'<ds:DigestMethod Algorithm="http://www.w3.org/2001/04/xmlenc#sha256"></ds:DigestMethod>'
        f'<ds:DigestValue>{POLICY_HASH}</ds:DigestValue>'
        f'</xades:SigPolicyHash>'
        f'</xades:SignaturePolicyId>'
        f'</xades:SignaturePolicyIdentifier>'
        f'<xades:SignerRole>'
        f'<xades:ClaimedRoles>'
        f'<xades:ClaimedRole>supplier</xades:ClaimedRole>'
        f'</xades:ClaimedRoles>'
        f'</xades:SignerRole>'
        f'</xades:SignedSignatureProperties>'
        f'</xades:SignedProperties>'
    )


def _build_signed_info(
    ref0_id, doc_digest,
    key_info_id, key_info_digest,
    sp_id, sp_digest,
) -> str:
    return (
        f'<ds:SignedInfo>'
        f'<ds:CanonicalizationMethod Algorithm="http://www.w3.org/TR/2001/REC-xml-c14n-20010315"></ds:CanonicalizationMethod>'
        f'<ds:SignatureMethod Algorithm="http://www.w3.org/2001/04/xmldsig-more#rsa-sha256"></ds:SignatureMethod>'
        f'<ds:Reference Id="{ref0_id}" URI="">'
        f'<ds:Transforms>'
        f'<ds:Transform Algorithm="http://www.w3.org/2000/09/xmldsig#enveloped-signature"></ds:Transform>'
        f'</ds:Transforms>'
        f'<ds:DigestMethod Algorithm="http://www.w3.org/2001/04/xmlenc#sha256"></ds:DigestMethod>'
        f'<ds:DigestValue>{doc_digest}</ds:DigestValue>'
        f'</ds:Reference>'
        f'<ds:Reference URI="#{key_info_id}">'
        f'<ds:DigestMethod Algorithm="http://www.w3.org/2001/04/xmlenc#sha256"></ds:DigestMethod>'
        f'<ds:DigestValue>{key_info_digest}</ds:DigestValue>'
        f'</ds:Reference>'
        f'<ds:Reference Type="http://uri.etsi.org/01903#SignedProperties" URI="#{sp_id}">'
        f'<ds:DigestMethod Algorithm="http://www.w3.org/2001/04/xmlenc#sha256"></ds:DigestMethod>'
        f'<ds:DigestValue>{sp_digest}</ds:DigestValue>'
        f'</ds:Reference>'
        f'</ds:SignedInfo>'
    )


def _insert_signature(root, sig_elem):
    """
    Insert the Signature element into the second ext:ExtensionContent
    of the UBLExtensions block.
    """
    ext_contents = root.findall(
        f'{{{NS_EXT}}}UBLExtensions'
        f'/{{{NS_EXT}}}UBLExtension'
        f'/{{{NS_EXT}}}ExtensionContent'
    )
    target = ext_contents[1] if len(ext_contents) >= 2 else (
        ext_contents[0] if ext_contents else root
    )
    target.append(sig_elem)
