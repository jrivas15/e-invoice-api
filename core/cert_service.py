import os
import base64
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from apps.tenants.models import Certificate


MASTER_KEY = base64.b64decode(os.environ['MASTER_KEY'])


def encrypt(data: bytes) -> str:
    aesgcm = AESGCM(MASTER_KEY)
    nonce = os.urandom(12)
    encrypted = aesgcm.encrypt(nonce, data, None)
    return base64.b64encode(nonce + encrypted).decode()


def decrypt(token: str) -> bytes:
    raw = base64.b64decode(token)
    nonce, encrypted = raw[:12], raw[12:]
    aesgcm = AESGCM(MASTER_KEY)
    return aesgcm.decrypt(nonce, encrypted, None)


def load_certificate(tenant) -> tuple[bytes, str]:
    """Returns (p12_bytes, password) ready to use in signer.py"""
    cert = Certificate.objects.get(tenant=tenant, active=True)
    p12 = decrypt(bytes(cert.p12_encrypted).decode())
    password = decrypt(cert.password_encrypted).decode()
    return p12, password
