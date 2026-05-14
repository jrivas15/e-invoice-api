"""
DIAN SOAP client — sends signed XML to DIAN's web service.

send_to_dian(signed_xml, p12_bytes, cert_password, config) -> dict
All ZIP/SOAP work done in memory (no disk I/O).
"""
import base64
import hashlib
import io
import logging
import uuid
import zipfile
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.serialization import pkcs12

# DIAN endpoints
ENDPOINTS = {
    'PRUEBAS':     'https://vpfe-hab.dian.gov.co/WcfDianCustomerServices.svc',
    'PRODUCCIÓN':  'https://vpfe.dian.gov.co/WcfDianCustomerServices.svc',
}

# WS-Security namespace
WSS_BASE = 'http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-'
WSU_NS   = WSS_BASE + 'wssecurity-utility-1.0.xsd'
WSSE_NS  = WSS_BASE + 'wssecurity-secext-1.0.xsd'
WSS_X509_TOKEN_PROFILE = (
    WSS_BASE + 'x509-token-profile-1.0#X509v3'
)
WSS_X509_TOKEN_REF = (
    WSS_BASE + 'x509-token-profile-1.0#X509SubjectKeyIdentifier'
)
DS_NS    = 'http://www.w3.org/2000/09/xmldsig#'
SOAP_NS  = 'http://www.w3.org/2003/05/soap-envelope'
WSA_NS   = 'http://www.w3.org/2005/08/addressing'
EXC_C14N = 'http://www.w3.org/2001/10/xml-exc-c14n#'
EC_NS    = 'http://www.w3.org/2001/10/xml-exc-c14n#'
SEND_BILL_SYNC_ACTION       = 'http://wcf.dian.colombia/IWcfDianCustomerServices/SendBillSync'
GET_STATUS_ACTION           = 'http://wcf.dian.colombia/IWcfDianCustomerServices/GetStatus'
SEND_TEST_SET_ASYNC_ACTION  = 'http://wcf.dian.colombia/IWcfDianCustomerServices/SendTestSetAsync'
GET_NUMBERING_RANGE_ACTION  = 'http://wcf.dian.colombia/IWcfDianCustomerServices/GetNumberingRange'


def send_to_dian(
    signed_xml: str,
    p12_bytes: bytes,
    cert_password: str,
    config,
) -> dict:
    """
    ZIP the signed XML and submit it to DIAN via SendBillSync.

    Parameters
    ----------
    signed_xml    : Signed invoice XML string
    p12_bytes     : PKCS#12 certificate bytes (for WS-Security)
    cert_password : Certificate password
    config        : FiscalConfig instance

    Returns
    -------
    dict with keys: code, errors, status_msg
    """
    ambiente = getattr(config, 'ambiente', 'PRUEBAS')
    endpoint = ENDPOINTS.get(ambiente, ENDPOINTS['PRUEBAS'])

    # --- Load certificate for WS-Security -----------------------------------
    pfx         = pkcs12.load_pkcs12(p12_bytes, cert_password.encode())
    private_key = pfx.key
    cert        = pfx.cert.certificate
    cert_der    = cert.public_bytes(serialization.Encoding.DER)
    cert_b64    = base64.b64encode(cert_der).decode()

    # --- Build ZIP in memory ------------------------------------------------
    zip_filename = _zip_filename(config, signed_xml)          # e.g. nit2026FEAI-001.zip
    xml_filename = zip_filename[:-4] + '.xml'                 # e.g. nit2026FEAI-001.xml
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(xml_filename, signed_xml.encode('utf-8'))
    zip_b64 = base64.b64encode(buf.getvalue()).decode()

    # --- Build SOAP envelope ------------------------------------------------
    token_id  = 'X509-98BE117EBB456766E81711755436072418'
    ts_id     = 'TS-98BE117EBB456766E81711755436086423'
    sig_id    = 'SIG-98BE117EBB456766E81711755436083422'
    ki_id     = 'KI-98BE117EBB456766E81711755436072419'
    str_id    = 'STR-98BE117EBB456766E81711755436072420'
    wsa_to_id = 'id-98BE117EBB456766E81711755436072421'

    from datetime import timedelta
    now        = datetime.now(timezone.utc)
    created    = now.strftime('%Y-%m-%dT%H:%M:%SZ')
    expires    = (now + timedelta(seconds=60)).strftime('%Y-%m-%dT%H:%M:%SZ')

    # wsa:To element — this is the element that gets signed (same approach as working code)
    wsa_to_xml = (
        f'<wsa:To'
        f' xmlns:soap="{SOAP_NS}"'
        f' xmlns:wcf="http://wcf.dian.colombia"'
        f' xmlns:wsa="{WSA_NS}"'
        f' xmlns:wsu="{WSU_NS}"'
        f' wsu:Id="{wsa_to_id}">{endpoint}</wsa:To>'
    )
    wsa_to_digest = base64.b64encode(
        hashlib.sha256(wsa_to_xml.encode('utf-8')).digest()
    ).decode()

    ts_xml = (
        f'<wsu:Timestamp wsu:Id="{ts_id}">'
        f'<wsu:Created>{created}</wsu:Created>'
        f'<wsu:Expires>{expires}</wsu:Expires>'
        f'</wsu:Timestamp>'
    )

    # SignedInfo content (shared between signing string and document)
    signed_info_content = (
        f'<ds:CanonicalizationMethod Algorithm="{EXC_C14N}">'
        f'<ec:InclusiveNamespaces xmlns:ec="{EC_NS}" PrefixList="wsa soap wcf"></ec:InclusiveNamespaces>'
        f'</ds:CanonicalizationMethod>'
        f'<ds:SignatureMethod Algorithm="http://www.w3.org/2001/04/xmldsig-more#rsa-sha256"></ds:SignatureMethod>'
        f'<ds:Reference URI="#{wsa_to_id}">'
        f'<ds:Transforms>'
        f'<ds:Transform Algorithm="{EXC_C14N}">'
        f'<ec:InclusiveNamespaces xmlns:ec="{EC_NS}" PrefixList="soap wcf"></ec:InclusiveNamespaces>'
        f'</ds:Transform>'
        f'</ds:Transforms>'
        f'<ds:DigestMethod Algorithm="http://www.w3.org/2001/04/xmlenc#sha256"></ds:DigestMethod>'
        f'<ds:DigestValue>{wsa_to_digest}</ds:DigestValue>'
        f'</ds:Reference>'
    )

    # String used for signing — includes explicit namespace declarations (matches working code)
    signed_info_for_signing = (
        f'<ds:SignedInfo'
        f' xmlns:ds="{DS_NS}"'
        f' xmlns:soap="{SOAP_NS}"'
        f' xmlns:wcf="http://wcf.dian.colombia"'
        f' xmlns:wsa="{WSA_NS}">'
        f'{signed_info_content}'
        f'</ds:SignedInfo>'
    )

    sig_bytes = private_key.sign(
        signed_info_for_signing.encode('utf-8'), padding.PKCS1v15(), hashes.SHA256()
    )
    sig_value = base64.b64encode(sig_bytes).decode()

    # In the document, SignedInfo has no extra namespace declarations —
    # they're inherited from ancestors (ds from Signature, soap/wcf from Envelope, wsa from Header)
    signed_info_for_doc = f'<ds:SignedInfo>{signed_info_content}</ds:SignedInfo>'

    soap_envelope = (
        f'<soap:Envelope xmlns:soap="{SOAP_NS}" xmlns:wcf="http://wcf.dian.colombia">'
        f'<soap:Header xmlns:wsa="{WSA_NS}">'
        f'<wsse:Security xmlns:wsse="{WSSE_NS}" xmlns:wsu="{WSU_NS}">'
        f'{ts_xml}'
        f'<wsse:BinarySecurityToken'
        f' EncodingType="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-soap-message-security-1.0#Base64Binary"'
        f' ValueType="{WSS_X509_TOKEN_PROFILE}"'
        f' wsu:Id="{token_id}">{cert_b64}</wsse:BinarySecurityToken>'
        f'<ds:Signature Id="{sig_id}" xmlns:ds="{DS_NS}">'
        f'{signed_info_for_doc}'
        f'<ds:SignatureValue>{sig_value}</ds:SignatureValue>'
        f'<ds:KeyInfo Id="{ki_id}">'
        f'<wsse:SecurityTokenReference wsu:Id="{str_id}">'
        f'<wsse:Reference URI="#{token_id}" ValueType="{WSS_X509_TOKEN_PROFILE}"/>'
        f'</wsse:SecurityTokenReference>'
        f'</ds:KeyInfo>'
        f'</ds:Signature>'
        f'</wsse:Security>'
        f'<wsa:Action>{SEND_BILL_SYNC_ACTION}</wsa:Action>'
        f'<wsa:To wsu:Id="{wsa_to_id}" xmlns:wsu="{WSU_NS}">{endpoint}</wsa:To>'
        f'</soap:Header>'
        f'<soap:Body>'
        f'<wcf:SendBillSync>'
        f'<wcf:fileName>{zip_filename}</wcf:fileName>'
        f'<wcf:contentFile>{zip_b64}</wcf:contentFile>'
        f'</wcf:SendBillSync>'
        f'</soap:Body>'
        f'</soap:Envelope>'
    )

    # --- Debug: guardar SOAP enviado ----------------------------------------
    # _save_debug(soap_envelope, 'soap_debug.xml')

    # --- HTTP call ----------------------------------------------------------
    headers = {
        'Accept':          'application/xml',
        'Content-Type':    'application/soap+xml',
        'Content-Length':  str(len(soap_envelope.encode('utf-8'))),
        'SOAPAction':      SEND_BILL_SYNC_ACTION,
        'Accept-Encoding': 'gzip,deflate',
    }
    # print(f'headers: {headers}')
    # print(f'Sending SOAP request to DIAN endpoint {endpoint} with body:\n{soap_envelope[:1000]}...')
    try:
        resp = requests.post(
            endpoint,
            data=soap_envelope.encode('utf-8'),
            headers=headers,
            timeout=60,
            verify=True,
        )
        logger.debug('DIAN response status: %s', resp.status_code)
        # print(f'DIAN response body: {resp.text}')
        # _save_debug(resp.text, f'dian_response_{zip_filename[:-4]}.xml')
        resp.raise_for_status()
        return _parse_response(resp.text, zip_filename)
    except requests.exceptions.HTTPError as exc:
        # _save_debug(exc.response.text, f'dian_response_{zip_filename[:-4]}_error.xml')
        return {
            'code': '99',
            'errors': [str(exc)],
            'status_msg': f'HTTP error: {exc.response.status_code}',
        }
    except requests.exceptions.RequestException as exc:
        # print(f'Connection error: {str(exc)}')
        return {
            'code': '99',
            'errors': [str(exc)],
            'status_msg': 'Connection error',
        }


def send_to_test_set(
    signed_xml: str,
    p12_bytes: bytes,
    cert_password: str,
    config,
    test_set_id: str,
) -> dict:
    """
    Envía una factura firmada al set de pruebas DIAN (operación SendTestSetAsync).
    El endpoint siempre es PRUEBAS — el set de pruebas solo existe en habilitación.

    Returns
    -------
    dict con claves: code ('00'|'99'), zip_key, errors, status_msg
    """
    endpoint = ENDPOINTS['PRUEBAS']

    pfx         = pkcs12.load_pkcs12(p12_bytes, cert_password.encode())
    private_key = pfx.key
    cert        = pfx.cert.certificate
    cert_der    = cert.public_bytes(serialization.Encoding.DER)
    cert_b64    = base64.b64encode(cert_der).decode()

    zip_filename = _zip_filename(config, signed_xml)
    xml_filename = zip_filename[:-4] + '.xml'
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(xml_filename, signed_xml.encode('utf-8'))
    zip_b64 = base64.b64encode(buf.getvalue()).decode()

    token_id  = 'X509-' + uuid.uuid4().hex.upper()
    ts_id     = 'TS-'   + uuid.uuid4().hex.upper()
    sig_id    = 'SIG-'  + uuid.uuid4().hex.upper()
    ki_id     = 'KI-'   + uuid.uuid4().hex.upper()
    str_id    = 'STR-'  + uuid.uuid4().hex.upper()
    wsa_to_id = 'id-'   + uuid.uuid4().hex.upper()

    from datetime import timedelta
    now     = datetime.now(timezone.utc)
    created = now.strftime('%Y-%m-%dT%H:%M:%SZ')
    expires = (now + timedelta(seconds=60)).strftime('%Y-%m-%dT%H:%M:%SZ')

    wsa_to_xml = (
        f'<wsa:To'
        f' xmlns:soap="{SOAP_NS}"'
        f' xmlns:wcf="http://wcf.dian.colombia"'
        f' xmlns:wsa="{WSA_NS}"'
        f' xmlns:wsu="{WSU_NS}"'
        f' wsu:Id="{wsa_to_id}">{endpoint}</wsa:To>'
    )
    wsa_to_digest = base64.b64encode(
        hashlib.sha256(wsa_to_xml.encode('utf-8')).digest()
    ).decode()

    ts_xml = (
        f'<wsu:Timestamp wsu:Id="{ts_id}">'
        f'<wsu:Created>{created}</wsu:Created>'
        f'<wsu:Expires>{expires}</wsu:Expires>'
        f'</wsu:Timestamp>'
    )

    signed_info_content = (
        f'<ds:CanonicalizationMethod Algorithm="{EXC_C14N}">'
        f'<ec:InclusiveNamespaces xmlns:ec="{EC_NS}" PrefixList="wsa soap wcf"></ec:InclusiveNamespaces>'
        f'</ds:CanonicalizationMethod>'
        f'<ds:SignatureMethod Algorithm="http://www.w3.org/2001/04/xmldsig-more#rsa-sha256"></ds:SignatureMethod>'
        f'<ds:Reference URI="#{wsa_to_id}">'
        f'<ds:Transforms><ds:Transform Algorithm="{EXC_C14N}">'
        f'<ec:InclusiveNamespaces xmlns:ec="{EC_NS}" PrefixList="soap wcf"></ec:InclusiveNamespaces>'
        f'</ds:Transform></ds:Transforms>'
        f'<ds:DigestMethod Algorithm="http://www.w3.org/2001/04/xmlenc#sha256"></ds:DigestMethod>'
        f'<ds:DigestValue>{wsa_to_digest}</ds:DigestValue>'
        f'</ds:Reference>'
    )
    signed_info_for_signing = (
        f'<ds:SignedInfo'
        f' xmlns:ds="{DS_NS}" xmlns:soap="{SOAP_NS}"'
        f' xmlns:wcf="http://wcf.dian.colombia" xmlns:wsa="{WSA_NS}">'
        f'{signed_info_content}'
        f'</ds:SignedInfo>'
    )
    sig_bytes = private_key.sign(
        signed_info_for_signing.encode('utf-8'), padding.PKCS1v15(), hashes.SHA256()
    )
    sig_value = base64.b64encode(sig_bytes).decode()
    signed_info_for_doc = f'<ds:SignedInfo>{signed_info_content}</ds:SignedInfo>'

    soap_envelope = (
        f'<soap:Envelope xmlns:soap="{SOAP_NS}" xmlns:wcf="http://wcf.dian.colombia">'
        f'<soap:Header xmlns:wsa="{WSA_NS}">'
        f'<wsse:Security xmlns:wsse="{WSSE_NS}" xmlns:wsu="{WSU_NS}">'
        f'{ts_xml}'
        f'<wsse:BinarySecurityToken'
        f' EncodingType="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-soap-message-security-1.0#Base64Binary"'
        f' ValueType="{WSS_X509_TOKEN_PROFILE}"'
        f' wsu:Id="{token_id}">{cert_b64}</wsse:BinarySecurityToken>'
        f'<ds:Signature Id="{sig_id}" xmlns:ds="{DS_NS}">'
        f'{signed_info_for_doc}'
        f'<ds:SignatureValue>{sig_value}</ds:SignatureValue>'
        f'<ds:KeyInfo Id="{ki_id}"><wsse:SecurityTokenReference wsu:Id="{str_id}">'
        f'<wsse:Reference URI="#{token_id}" ValueType="{WSS_X509_TOKEN_PROFILE}"/>'
        f'</wsse:SecurityTokenReference></ds:KeyInfo>'
        f'</ds:Signature>'
        f'</wsse:Security>'
        f'<wsa:Action>{SEND_TEST_SET_ASYNC_ACTION}</wsa:Action>'
        f'<wsa:To wsu:Id="{wsa_to_id}" xmlns:wsu="{WSU_NS}">{endpoint}</wsa:To>'
        f'</soap:Header>'
        f'<soap:Body>'
        f'<wcf:SendTestSetAsync>'
        f'<wcf:fileName>{zip_filename}</wcf:fileName>'
        f'<wcf:contentFile>{zip_b64}</wcf:contentFile>'
        f'<wcf:testSetId>{test_set_id}</wcf:testSetId>'
        f'</wcf:SendTestSetAsync>'
        f'</soap:Body>'
        f'</soap:Envelope>'
    )

    headers = {
        'Accept':          'application/xml',
        'Content-Type':    'application/soap+xml',
        'Content-Length':  str(len(soap_envelope.encode('utf-8'))),
        'SOAPAction':      SEND_TEST_SET_ASYNC_ACTION,
        'Accept-Encoding': 'gzip,deflate',
    }
    try:
        resp = requests.post(
            endpoint, data=soap_envelope.encode('utf-8'),
            headers=headers, timeout=60, verify=True,
        )
        logger.debug('DIAN SendTestSetAsync response status: %s', resp.status_code)
        resp.raise_for_status()
        return _parse_test_set_response(resp.text)
    except requests.exceptions.HTTPError as exc:
        return {
            'code': '99',
            'zip_key': '',
            'errors': [str(exc)],
            'status_msg': f'HTTP error: {exc.response.status_code}',
        }
    except requests.exceptions.RequestException as exc:
        return {
            'code': '99',
            'zip_key': '',
            'errors': [str(exc)],
            'status_msg': 'Connection error',
        }


def _parse_test_set_response(soap_response: str) -> dict:
    """Parse SendTestSetAsync SOAP response → {code, zip_key, errors, status_msg}."""
    try:
        from lxml import etree
        root = etree.fromstring(soap_response.encode('utf-8'))

        UPLOAD = 'http://schemas.datacontract.org/2004/07/UploadDocumentResponse'
        ARR    = 'http://schemas.microsoft.com/2003/10/Serialization/Arrays'

        zip_key = ''
        for el in root.iter(f'{{{UPLOAD}}}ZipKey'):
            zip_key = (el.text or '').strip()
            break

        errors = []
        for el in root.iter(f'{{{UPLOAD}}}ErrorMessage'):
            for s in el.findall(f'{{{ARR}}}string'):
                if s.text and s.text.strip():
                    errors.append(s.text.strip())

        if zip_key:
            logger.info('SendTestSetAsync OK — ZipKey=%s', zip_key)
            return {
                'code': '00',
                'zip_key': zip_key,
                'errors': errors,
                'status_msg': 'Enviado al set de pruebas',
            }

        logger.warning('SendTestSetAsync sin ZipKey — errors=%s', errors)
        return {
            'code': '99',
            'zip_key': '',
            'errors': errors or ['Respuesta sin ZipKey'],
            'status_msg': 'Set de pruebas rechazó el documento',
        }
    except Exception as exc:
        return {
            'code': '99',
            'zip_key': '',
            'errors': [str(exc)],
            'status_msg': 'Failed to parse SendTestSetAsync response',
        }


def get_application_response(cufe: str, p12_bytes: bytes, cert_password: str, config) -> str:
    """
    Llama al endpoint GetStatus de la DIAN con el CUFE y retorna el
    ApplicationResponse XML decodificado (string UTF-8).

    Usar como fallback cuando el XML no está disponible desde el flujo normal
    (e.g., reenvío de email, consulta manual).

    Raises RuntimeError si la DIAN no retorna el XML.
    """
    from datetime import timedelta

    ambiente = getattr(config, 'ambiente', 'PRUEBAS')
    endpoint = ENDPOINTS.get(ambiente, ENDPOINTS['PRUEBAS'])

    pfx         = pkcs12.load_pkcs12(p12_bytes, cert_password.encode())
    private_key = pfx.key
    cert        = pfx.cert.certificate
    cert_der    = cert.public_bytes(serialization.Encoding.DER)
    cert_b64    = base64.b64encode(cert_der).decode()

    token_id  = 'X509-' + uuid.uuid4().hex.upper()
    ts_id     = 'TS-'   + uuid.uuid4().hex.upper()
    sig_id    = 'SIG-'  + uuid.uuid4().hex.upper()
    ki_id     = 'KI-'   + uuid.uuid4().hex.upper()
    str_id    = 'STR-'  + uuid.uuid4().hex.upper()
    wsa_to_id = 'id-'   + uuid.uuid4().hex.upper()

    now     = datetime.now(timezone.utc)
    created = now.strftime('%Y-%m-%dT%H:%M:%SZ')
    expires = (now + timedelta(seconds=60)).strftime('%Y-%m-%dT%H:%M:%SZ')

    wsa_to_xml = (
        f'<wsa:To'
        f' xmlns:soap="{SOAP_NS}"'
        f' xmlns:wcf="http://wcf.dian.colombia"'
        f' xmlns:wsa="{WSA_NS}"'
        f' xmlns:wsu="{WSU_NS}"'
        f' wsu:Id="{wsa_to_id}">{endpoint}</wsa:To>'
    )
    wsa_to_digest = base64.b64encode(
        hashlib.sha256(wsa_to_xml.encode('utf-8')).digest()
    ).decode()

    ts_xml = (
        f'<wsu:Timestamp wsu:Id="{ts_id}">'
        f'<wsu:Created>{created}</wsu:Created>'
        f'<wsu:Expires>{expires}</wsu:Expires>'
        f'</wsu:Timestamp>'
    )

    signed_info_content = (
        f'<ds:CanonicalizationMethod Algorithm="{EXC_C14N}">'
        f'<ec:InclusiveNamespaces xmlns:ec="{EC_NS}" PrefixList="wsa soap wcf"></ec:InclusiveNamespaces>'
        f'</ds:CanonicalizationMethod>'
        f'<ds:SignatureMethod Algorithm="http://www.w3.org/2001/04/xmldsig-more#rsa-sha256"></ds:SignatureMethod>'
        f'<ds:Reference URI="#{wsa_to_id}">'
        f'<ds:Transforms><ds:Transform Algorithm="{EXC_C14N}">'
        f'<ec:InclusiveNamespaces xmlns:ec="{EC_NS}" PrefixList="soap wcf"></ec:InclusiveNamespaces>'
        f'</ds:Transform></ds:Transforms>'
        f'<ds:DigestMethod Algorithm="http://www.w3.org/2001/04/xmlenc#sha256"></ds:DigestMethod>'
        f'<ds:DigestValue>{wsa_to_digest}</ds:DigestValue>'
        f'</ds:Reference>'
    )
    signed_info_for_signing = (
        f'<ds:SignedInfo'
        f' xmlns:ds="{DS_NS}" xmlns:soap="{SOAP_NS}"'
        f' xmlns:wcf="http://wcf.dian.colombia" xmlns:wsa="{WSA_NS}">'
        f'{signed_info_content}'
        f'</ds:SignedInfo>'
    )
    sig_bytes = private_key.sign(
        signed_info_for_signing.encode('utf-8'), padding.PKCS1v15(), hashes.SHA256()
    )
    sig_value = base64.b64encode(sig_bytes).decode()
    signed_info_for_doc = f'<ds:SignedInfo>{signed_info_content}</ds:SignedInfo>'

    soap_envelope = (
        f'<soap:Envelope xmlns:soap="{SOAP_NS}" xmlns:wcf="http://wcf.dian.colombia">'
        f'<soap:Header xmlns:wsa="{WSA_NS}">'
        f'<wsse:Security xmlns:wsse="{WSSE_NS}" xmlns:wsu="{WSU_NS}">'
        f'{ts_xml}'
        f'<wsse:BinarySecurityToken'
        f' EncodingType="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-soap-message-security-1.0#Base64Binary"'
        f' ValueType="{WSS_X509_TOKEN_PROFILE}"'
        f' wsu:Id="{token_id}">{cert_b64}</wsse:BinarySecurityToken>'
        f'<ds:Signature Id="{sig_id}" xmlns:ds="{DS_NS}">'
        f'{signed_info_for_doc}'
        f'<ds:SignatureValue>{sig_value}</ds:SignatureValue>'
        f'<ds:KeyInfo Id="{ki_id}"><wsse:SecurityTokenReference wsu:Id="{str_id}">'
        f'<wsse:Reference URI="#{token_id}" ValueType="{WSS_X509_TOKEN_PROFILE}"/>'
        f'</wsse:SecurityTokenReference></ds:KeyInfo>'
        f'</ds:Signature>'
        f'</wsse:Security>'
        f'<wsa:Action>{GET_STATUS_ACTION}</wsa:Action>'
        f'<wsa:To wsu:Id="{wsa_to_id}" xmlns:wsu="{WSU_NS}">{endpoint}</wsa:To>'
        f'</soap:Header>'
        f'<soap:Body>'
        f'<wcf:GetStatus>'
        f'<wcf:trackId>{cufe}</wcf:trackId>'
        f'</wcf:GetStatus>'
        f'</soap:Body>'
        f'</soap:Envelope>'
    )

    headers = {
        'Accept':          'application/xml',
        'Content-Type':    'application/soap+xml',
        'Content-Length':  str(len(soap_envelope.encode('utf-8'))),
        'SOAPAction':      GET_STATUS_ACTION,
        'Accept-Encoding': 'gzip,deflate',
    }
    resp = requests.post(endpoint, data=soap_envelope.encode('utf-8'),
                         headers=headers, timeout=30, verify=True)
    resp.raise_for_status()

    from lxml import etree
    root = etree.fromstring(resp.text.encode('utf-8'))
    MS = 'http://schemas.datacontract.org/2004/07/DianResponse'
    b64_node = None
    for el in root.iter(f'{{{MS}}}XmlBase64Bytes'):
        b64_node = el
        break
    if b64_node is None or not b64_node.text:
        raise RuntimeError(f'GetStatus: no XmlBase64Bytes in response for CUFE={cufe}')

    return base64.b64decode(b64_node.text.strip()).decode('utf-8', errors='replace')


def get_numbering_range(p12_bytes: bytes, cert_password: str, config) -> list[dict]:
    """
    Consulta la operación GetNumberingRange de la DIAN — devuelve las
    resoluciones de facturación autorizadas para el emisor.

    Returns
    -------
    list[dict] con claves: resolution_number, resolution_date, prefix,
        from_number, to_number, valid_date_from, valid_date_to, technical_key
    """
    from datetime import timedelta

    ambiente = getattr(config, 'ambiente', 'PRUEBAS')
    endpoint = ENDPOINTS.get(ambiente, ENDPOINTS['PRUEBAS'])

    pfx         = pkcs12.load_pkcs12(p12_bytes, cert_password.encode())
    private_key = pfx.key
    cert        = pfx.cert.certificate
    cert_der    = cert.public_bytes(serialization.Encoding.DER)
    cert_b64    = base64.b64encode(cert_der).decode()

    token_id  = 'X509-' + uuid.uuid4().hex.upper()
    ts_id     = 'TS-'   + uuid.uuid4().hex.upper()
    sig_id    = 'SIG-'  + uuid.uuid4().hex.upper()
    ki_id     = 'KI-'   + uuid.uuid4().hex.upper()
    str_id    = 'STR-'  + uuid.uuid4().hex.upper()
    wsa_to_id = 'id-'   + uuid.uuid4().hex.upper()

    now     = datetime.now(timezone.utc)
    created = now.strftime('%Y-%m-%dT%H:%M:%SZ')
    expires = (now + timedelta(seconds=60)).strftime('%Y-%m-%dT%H:%M:%SZ')

    wsa_to_xml = (
        f'<wsa:To'
        f' xmlns:soap="{SOAP_NS}"'
        f' xmlns:wcf="http://wcf.dian.colombia"'
        f' xmlns:wsa="{WSA_NS}"'
        f' xmlns:wsu="{WSU_NS}"'
        f' wsu:Id="{wsa_to_id}">{endpoint}</wsa:To>'
    )
    wsa_to_digest = base64.b64encode(
        hashlib.sha256(wsa_to_xml.encode('utf-8')).digest()
    ).decode()

    ts_xml = (
        f'<wsu:Timestamp wsu:Id="{ts_id}">'
        f'<wsu:Created>{created}</wsu:Created>'
        f'<wsu:Expires>{expires}</wsu:Expires>'
        f'</wsu:Timestamp>'
    )

    signed_info_content = (
        f'<ds:CanonicalizationMethod Algorithm="{EXC_C14N}">'
        f'<ec:InclusiveNamespaces xmlns:ec="{EC_NS}" PrefixList="wsa soap wcf"></ec:InclusiveNamespaces>'
        f'</ds:CanonicalizationMethod>'
        f'<ds:SignatureMethod Algorithm="http://www.w3.org/2001/04/xmldsig-more#rsa-sha256"></ds:SignatureMethod>'
        f'<ds:Reference URI="#{wsa_to_id}">'
        f'<ds:Transforms><ds:Transform Algorithm="{EXC_C14N}">'
        f'<ec:InclusiveNamespaces xmlns:ec="{EC_NS}" PrefixList="soap wcf"></ec:InclusiveNamespaces>'
        f'</ds:Transform></ds:Transforms>'
        f'<ds:DigestMethod Algorithm="http://www.w3.org/2001/04/xmlenc#sha256"></ds:DigestMethod>'
        f'<ds:DigestValue>{wsa_to_digest}</ds:DigestValue>'
        f'</ds:Reference>'
    )
    signed_info_for_signing = (
        f'<ds:SignedInfo'
        f' xmlns:ds="{DS_NS}" xmlns:soap="{SOAP_NS}"'
        f' xmlns:wcf="http://wcf.dian.colombia" xmlns:wsa="{WSA_NS}">'
        f'{signed_info_content}'
        f'</ds:SignedInfo>'
    )
    sig_bytes = private_key.sign(
        signed_info_for_signing.encode('utf-8'), padding.PKCS1v15(), hashes.SHA256()
    )
    sig_value = base64.b64encode(sig_bytes).decode()
    signed_info_for_doc = f'<ds:SignedInfo>{signed_info_content}</ds:SignedInfo>'

    soap_envelope = (
        f'<soap:Envelope xmlns:soap="{SOAP_NS}" xmlns:wcf="http://wcf.dian.colombia">'
        f'<soap:Header xmlns:wsa="{WSA_NS}">'
        f'<wsse:Security xmlns:wsse="{WSSE_NS}" xmlns:wsu="{WSU_NS}">'
        f'{ts_xml}'
        f'<wsse:BinarySecurityToken'
        f' EncodingType="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-soap-message-security-1.0#Base64Binary"'
        f' ValueType="{WSS_X509_TOKEN_PROFILE}"'
        f' wsu:Id="{token_id}">{cert_b64}</wsse:BinarySecurityToken>'
        f'<ds:Signature Id="{sig_id}" xmlns:ds="{DS_NS}">'
        f'{signed_info_for_doc}'
        f'<ds:SignatureValue>{sig_value}</ds:SignatureValue>'
        f'<ds:KeyInfo Id="{ki_id}"><wsse:SecurityTokenReference wsu:Id="{str_id}">'
        f'<wsse:Reference URI="#{token_id}" ValueType="{WSS_X509_TOKEN_PROFILE}"/>'
        f'</wsse:SecurityTokenReference></ds:KeyInfo>'
        f'</ds:Signature>'
        f'</wsse:Security>'
        f'<wsa:Action>{GET_NUMBERING_RANGE_ACTION}</wsa:Action>'
        f'<wsa:To wsu:Id="{wsa_to_id}" xmlns:wsu="{WSU_NS}">{endpoint}</wsa:To>'
        f'</soap:Header>'
        f'<soap:Body>'
        f'<wcf:GetNumberingRange>'
        f'<wcf:accountCode>{config.nit}</wcf:accountCode>'
        f'<wcf:accountCodeT>{config.nit}</wcf:accountCodeT>'
        f'<wcf:softwareCode>{config.software_id}</wcf:softwareCode>'
        f'</wcf:GetNumberingRange>'
        f'</soap:Body>'
        f'</soap:Envelope>'
    )

    headers = {
        'Accept':          'application/xml',
        'Content-Type':    'application/soap+xml',
        'Content-Length':  str(len(soap_envelope.encode('utf-8'))),
        'SOAPAction':      GET_NUMBERING_RANGE_ACTION,
        'Accept-Encoding': 'gzip,deflate',
    }
    resp = requests.post(endpoint, data=soap_envelope.encode('utf-8'),
                         headers=headers, timeout=30, verify=True)
    resp.raise_for_status()

    return _parse_numbering_range(resp.text)


def _parse_numbering_range(soap_response: str) -> list[dict]:
    """Extrae las resoluciones del response de GetNumberingRange."""
    from lxml import etree
    root = etree.fromstring(soap_response.encode('utf-8'))
    NS = 'http://schemas.datacontract.org/2004/07/DianResponse'

    def _txt(parent, tag):
        node = parent.find(f'{{{NS}}}{tag}')
        return (node.text or '').strip() if node is not None and node.text else ''

    results = []
    for item in root.iter(f'{{{NS}}}NumberRangeResponse'):
        results.append({
            'resolution_number': _txt(item, 'ResolutionNumber'),
            'resolution_date':   _txt(item, 'ResolutionDate'),
            'prefix':            _txt(item, 'Prefix'),
            'from_number':       _txt(item, 'FromNumber'),
            'to_number':         _txt(item, 'ToNumber'),
            'valid_date_from':   _txt(item, 'ValidDateFrom'),
            'valid_date_to':     _txt(item, 'ValidDateTo'),
            'technical_key':     _txt(item, 'TechnicalKey'),
        })

    logger.info('GetNumberingRange — %d resoluciones encontradas', len(results))
    return results


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _save_debug(content: str, filename: str):
    import os
    path = os.path.join(os.path.dirname(__file__), '..', filename)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)

def _zip_filename(config, signed_xml: str) -> str:
    """
    DIAN ZIP filename convention:
    nit_year_consecutive.xml  →  nit_year_consecutive.zip
    We use the invoice ID extracted from <cbc:ID> in the XML.
    """
    try:
        from lxml import etree
        root = etree.fromstring(signed_xml.encode('utf-8'))
        ns = 'urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2'
        id_el = root.find(f'{{{ns}}}ID')
        inv_number = id_el.text if id_el is not None else 'invoice'
    except Exception:
        inv_number = 'invoice'

    nit  = config.nit
    year = datetime.now().year
    return f'{nit}{year}{inv_number}.zip'


def _parse_response(soap_response: str, zip_filename: str = '') -> dict:
    """Parse DIAN SendBillSync SOAP response into a normalised dict."""
    try:
        from lxml import etree
        root = etree.fromstring(soap_response.encode('utf-8'))

        # Navigate to SendBillSyncResult — namespace-agnostic search
        result = None
        for el in root.iter():
            if el.tag.endswith('}SendBillSyncResult') or el.tag == 'SendBillSyncResult':
                result = el
                break

        if result is None:
            return {
                'code': '99',
                'errors': ['Unexpected SOAP response'],
                'status_msg': soap_response[:500],
            }

        WCF = 'http://wcf.dian.colombia'
        MS  = 'http://schemas.datacontract.org/2004/07/DianResponse'
        ARR = 'http://schemas.microsoft.com/2003/10/Serialization/Arrays'

        def _txt(ns, tag, parent=None):
            node = (parent or result).find(f'{{{ns}}}{tag}')
            return (node.text or '').strip() if node is not None else ''

        is_valid    = _txt(MS, 'IsValid').lower() == 'true'
        status_code = _txt(MS, 'StatusCode') or ('00' if is_valid else '99')
        status_desc = _txt(MS, 'StatusDescription')
        status_msg  = _txt(MS, 'StatusMessage')
        doc_key     = _txt(MS, 'XmlDocumentKey')

        # ErrorMessage strings (can be warnings even when IsValid=true)
        notifications = []
        err_node = result.find(f'{{{MS}}}ErrorMessage')
        if err_node is not None:
            for s in err_node.findall(f'{{{ARR}}}string'):
                if s.text and s.text.strip():
                    notifications.append(s.text.strip())

        # Decode ApplicationResponse (XmlBase64Bytes) for detailed validation lines
        validation_lines = []
        b64_node = result.find(f'{{{MS}}}XmlBase64Bytes')
        if b64_node is not None and b64_node.text:
            try:
                ar_xml = base64.b64decode(b64_node.text.strip())
                label = zip_filename[:-4] if zip_filename else 'unknown'
                # _save_debug(ar_xml.decode('utf-8', errors='replace'), f'application_response_{label}.xml')
                ar_root = etree.fromstring(ar_xml)
                CBC = 'urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2'
                CAC = 'urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2'
                for doc_resp in ar_root.iter(f'{{{CAC}}}DocumentResponse'):
                    for line_resp in doc_resp.findall(f'{{{CAC}}}LineResponse'):
                        line_id_el  = line_resp.find(f'{{{CAC}}}LineReference/{{{CBC}}}LineID')
                        resp_el     = line_resp.find(f'{{{CAC}}}Response')
                        if resp_el is None:
                            continue
                        code_el = resp_el.find(f'{{{CBC}}}ResponseCode')
                        desc_el = resp_el.find(f'{{{CBC}}}Description')
                        validation_lines.append({
                            'line':        line_id_el.text if line_id_el is not None else '',
                            'rule':        (code_el.text or '').strip() if code_el is not None else '',
                            'description': (desc_el.text or '').strip() if desc_el is not None else '',
                        })
            except Exception as parse_exc:
                notifications.append(f'[ApplicationResponse parse error: {parse_exc}]')

        ar_xml_str = ''
        if b64_node is not None and b64_node.text:
            try:
                ar_xml_str = base64.b64decode(b64_node.text.strip()).decode('utf-8', errors='replace')
            except Exception:
                pass

        result_dict = {
            'code':                    '00' if is_valid else status_code,
            'is_valid':                is_valid,
            'status_description':      status_desc,
            'status_message':          status_msg,
            'document_key':            doc_key,
            'notifications':           notifications,
            'validation_lines':        validation_lines,
            'application_response_xml': ar_xml_str,
        }

        logger.info(
            'DIAN Response — is_valid=%s code=%s desc=%s',
            is_valid, result_dict['code'], status_desc,
        )

        return result_dict

    except Exception as exc:
        return {
            'code': '99',
            'errors': [str(exc)],
            'status_msg': 'Failed to parse DIAN response',
        }
