import datetime
import uuid
import hashlib
import secrets
from django.db import models


class Tenant(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=200)
    api_key_hash = models.CharField(max_length=64, unique=True)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'tenants'

    def __str__(self):
        return self.name

    @classmethod
    def generate_api_key(cls):
        """Returns (raw_key, hash) — store hash, give raw_key to client."""
        raw = secrets.token_urlsafe(32)
        hashed = hashlib.sha256(raw.encode()).hexdigest()
        return raw, hashed


TRIBUTOS = [
    ('01', '01 — IVA (Impuesto sobre las Ventas)'),
    ('02', '02 — IC (Impuesto al Consumo Departamental Nominal)'),
    ('03', '03 — ICA (Impuesto de Industria, Comercio y Aviso)'),
    ('04', '04 — INC (Impuesto Nacional al Consumo)'),
    ('05', '05 — ReteIVA (Retención sobre el IVA)'),
    ('06', '06 — ReteRenta (Retención sobre Renta)'),
    ('07', '07 — ReteICA (Retención sobre el ICA)'),
    ('08', '08 — IC Porcentual (Impuesto al Consumo Departamental Porcentual)'),
    ('20', '20 — FtoHorticultura (Cuota de Fomento Hortifrutícula)'),
    ('21', '21 — Timbre (Impuesto de Timbre)'),
    ('22', '22 — INC Bolsas (Impuesto Nacional al Consumo de Bolsa Plástica)'),
    ('23', '23 — INCarbono (Impuesto Nacional del Carbono)'),
    ('24', '24 — INCombustibles (Impuesto Nacional a los Combustibles)'),
    ('25', '25 — Sobretasa Combustibles'),
    ('26', '26 — Sordicom (Contribución minoristas - Combustibles)'),
    ('30', '30 — IC Datos (Impuesto al Consumo de Datos)'),
    ('32', '32 — ICL (Impuesto al Consumo de Licores)'),
    ('33', '33 — INPP (Impuesto nacional productos plásticos)'),
    ('34', '34 — IBUA (Impuesto a las bebidas ultraprocesadas azucaradas)'),
    ('35', '35 — ICUI (Impuesto a productos comestibles ultraprocesados)'),
    ('36', '36 — ADV (Ad Valorem)'),
    ('ZZ', 'ZZ — Otros tributos, tasas, contribuciones y similares'),
]

# Lookup: code → short name  (used to auto-fill tax_scheme_name)
TRIBUTOS_NAMES = {code: label.split(' — ')[1].split(' (')[0] for code, label in TRIBUTOS}

TAX_RESPONSIBILITIES = [
    ('O-13',    'O-13 — Gran contribuyente'),
    ('O-15',    'O-15 — Autorretenedor'),
    ('O-23',    'O-23 — Agente de retención IVA'),
    ('O-47',    'O-47 — Régimen simple de tributación'),
    ('R-99-PN', 'R-99-PN — No aplica – Otros'),
]


class FiscalConfig(models.Model):
    """Billing data required by DIAN for every invoice."""

    class PersonType(models.TextChoices):
        LEGAL = '1', 'Legal entity'
        NATURAL = '2', 'Natural person'

    tenant = models.OneToOneField(
        Tenant, on_delete=models.CASCADE, related_name='fiscal_config'
    )
    # Issuer identity
    legal_name = models.CharField(max_length=300)
    trade_name = models.CharField(max_length=300, blank=True, verbose_name='commercial name')
    nit = models.CharField(max_length=20)
    check_digit = models.CharField(max_length=1)

    # Address (required by DIAN)
    address = models.CharField(max_length=300, blank=True)
    city_code = models.CharField(max_length=10)
    city_name = models.CharField(max_length=100)
    department_code = models.CharField(max_length=10)
    country_code = models.CharField(max_length=3, default='CO')

    # Tax classification
    person_type = models.CharField(max_length=1, choices=PersonType.choices)
    tax_responsibilities = models.JSONField(default=list)

    # Invoice resolution — defaults: DIAN habilitación test range
    invoice_prefix    = models.CharField(max_length=10, blank=True, default='SETP')
    resolution_number = models.CharField(max_length=50, default='18760000001')
    resolution_date   = models.DateField(default=datetime.date(2019, 1, 19))
    resolution_end_date = models.DateField(null=True, blank=True, default=datetime.date(2030, 1, 19))
    range_start       = models.IntegerField(default=990000000)
    range_end         = models.IntegerField(default=995000000)
    current_number    = models.IntegerField(default=990000000)
    # Consecutivo independiente cuando ambiente=PRUEBAS (rango de TestResolution)
    test_current_number = models.IntegerField(default=990000000)

    # Contact
    phone = models.CharField(max_length=20, blank=True)
    email = models.EmailField()

    # DIAN software registration
    software_id = models.CharField(max_length=100, blank=True)
    software_pin = models.CharField(max_length=100, blank=True)
    clave_tecnica = models.CharField(max_length=200, blank=True, default='fc8eac422eba16e22ffd8c6f94b3f40a6e38162c')
    ambiente = models.CharField(
        max_length=20,
        choices=[('PRUEBAS', 'Pruebas'), ('PRODUCCIÓN', 'Producción')],
        default='PRUEBAS',
    )
    test_set_id = models.CharField(
        max_length=100,
        blank=True,
        help_text='UUID del set de pruebas DIAN. Solo necesario durante habilitación.',
    )

    # Tax scheme of issuer
    tax_scheme_id = models.CharField(max_length=10, default='01', choices=TRIBUTOS)
    tax_scheme_name = models.CharField(max_length=50, default='IVA')

    # Fiscal address
    fiscal_municipality = models.ForeignKey(
        'Municipality', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='+', verbose_name='Municipio fiscal',
    )
    fiscal_city_code = models.CharField(max_length=10, blank=True)
    fiscal_city_name = models.CharField(max_length=100, blank=True)
    fiscal_department = models.CharField(max_length=100, blank=True)
    fiscal_department_code = models.CharField(max_length=10, blank=True)
    fiscal_address = models.CharField(max_length=300, blank=True)

    # Registered address
    registered_municipality = models.ForeignKey(
        'Municipality', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='+', verbose_name='Municipio registrado',
    )

    # Postal code
    postal_code = models.CharField(max_length=10, blank=True)

    class Meta:
        db_table = 'fiscal_configs'

    def __str__(self):
        return f"{self.legal_name} ({self.nit})"


class Municipality(models.Model):
    """Colombian municipalities — DANE codes, used for address autocomplete."""
    department_code = models.CharField(max_length=2)
    department_name = models.CharField(max_length=100)
    city_code       = models.CharField(max_length=5, unique=True)
    city_name       = models.CharField(max_length=150)

    class Meta:
        db_table = 'municipalities'
        ordering = ['department_code', 'city_name']
        verbose_name = 'Municipio'
        verbose_name_plural = 'Municipios'

    def __str__(self):
        return f"{self.city_name} — {self.department_name} ({self.city_code})"


_TEST_OVERRIDE_FIELDS = (
    'invoice_prefix', 'resolution_number', 'resolution_date',
    'resolution_end_date', 'range_start', 'range_end', 'clave_tecnica',
)


def apply_test_resolution(config):
    """
    Si `config.ambiente == 'PRUEBAS'`, sustituye en memoria los campos de
    resolución del FiscalConfig con los del singleton TestResolution.
    No persiste cambios — el caller no debe llamar `.save()` después.
    """
    if config.ambiente != 'PRUEBAS':
        return config
    test = TestResolution.get_solo()
    for field in _TEST_OVERRIDE_FIELDS:
        setattr(config, field, getattr(test, field))
    return config


class TestResolution(models.Model):
    """
    Resolución de facturación de pruebas — singleton compartido por todos los tenants
    cuando `FiscalConfig.ambiente == 'PRUEBAS'`. Modificable desde admin.
    """
    invoice_prefix      = models.CharField(max_length=10, default='SETP')
    resolution_number   = models.CharField(max_length=50, default='18760000001')
    resolution_date     = models.DateField(default=datetime.date(2019, 1, 19))
    resolution_end_date = models.DateField(default=datetime.date(2030, 1, 19))
    range_start         = models.IntegerField(default=990000000)
    range_end           = models.IntegerField(default=995000000)
    clave_tecnica       = models.CharField(
        max_length=200,
        default='fc8eac422eba16e22ffd8c6f94b3f40a6e38162c',
    )
    updated_at          = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'test_resolution'
        verbose_name = 'Resolución de pruebas (global)'
        verbose_name_plural = 'Resolución de pruebas (global)'

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        pass

    @classmethod
    def get_solo(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    def __str__(self):
        return f'Resolución pruebas: {self.invoice_prefix} {self.resolution_number}'


class Certificate(models.Model):
    """p12 certificate per tenant, AES-256 encrypted at rest."""
    tenant = models.ForeignKey(
        Tenant, on_delete=models.CASCADE, related_name='certificates'
    )
    p12_encrypted = models.BinaryField()
    password_encrypted = models.CharField(max_length=300)
    expiry_date = models.DateField()
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'certificates'

    def __str__(self):
        return f"Certificado — {self.tenant}"
