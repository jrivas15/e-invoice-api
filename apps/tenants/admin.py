from django import forms
from django.contrib import admin, messages
from django.http import HttpResponseRedirect
from django.urls import path, reverse
from django.utils.html import format_html
from django.utils.safestring import mark_safe

from core.cert_service import encrypt
from .models import (
    Certificate, FiscalConfig, Municipality, TAX_RESPONSIBILITIES,
    TestResolution, TRIBUTOS_NAMES, Tenant,
)


# ---------------------------------------------------------------------------
# Municipality — registered for autocomplete only
# ---------------------------------------------------------------------------

@admin.register(Municipality)
class MunicipalityAdmin(admin.ModelAdmin):
    search_fields = ('city_name', 'city_code', 'department_name')
    list_display  = ('city_name', 'department_name', 'city_code')
    list_filter   = ('department_name',)
    # Read-only — data comes from the CSV migration
    def has_add_permission(self, request):    return False
    def has_change_permission(self, request, obj=None): return False
    def has_delete_permission(self, request, obj=None): return False


# ---------------------------------------------------------------------------
# FiscalConfig form — syncs text fields from municipality FK on save
# ---------------------------------------------------------------------------

class FiscalConfigForm(forms.ModelForm):
    tax_responsibilities = forms.MultipleChoiceField(
        label='Responsabilidades fiscales',
        choices=TAX_RESPONSIBILITIES,
        widget=forms.CheckboxSelectMultiple,
        required=False,
    )

    class Meta:
        model  = FiscalConfig
        fields = '__all__'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Pre-select from the existing JSON list
        if self.instance.pk and self.instance.tax_responsibilities:
            self.initial['tax_responsibilities'] = self.instance.tax_responsibilities

    def clean_tax_responsibilities(self):
        value = self.cleaned_data.get('tax_responsibilities')
        if not value:
            raise forms.ValidationError('Debes seleccionar al menos una responsabilidad fiscal.')
        return value

    def save(self, commit=True):
        instance = super().save(commit=False)

        fm = self.cleaned_data.get('fiscal_municipality')
        if fm:
            instance.fiscal_city_code       = fm.city_code
            instance.fiscal_city_name       = fm.city_name
            instance.fiscal_department      = fm.department_name
            instance.fiscal_department_code = fm.department_code

        rm = self.cleaned_data.get('registered_municipality')
        if rm:
            instance.city_code       = rm.city_code
            instance.city_name       = rm.city_name
            instance.department_code = rm.department_code

        tax_id = self.cleaned_data.get('tax_scheme_id')
        if tax_id:
            instance.tax_scheme_name = TRIBUTOS_NAMES.get(tax_id, tax_id)

        if commit:
            instance.save()
        return instance


# ---------------------------------------------------------------------------
# Certificate form — handles p12 upload + password encryption transparently
# ---------------------------------------------------------------------------

class CertificateForm(forms.ModelForm):
    p12_file = forms.FileField(
        label='Archivo .p12 / .pfx',
        required=False,
        help_text='Solo necesario al crear o reemplazar el certificado.',
    )
    cert_password = forms.CharField(
        label='Contraseña del certificado',
        required=False,
        widget=forms.PasswordInput(render_value=False),
        help_text='Solo necesario al crear o reemplazar el certificado.',
    )

    class Meta:
        model  = Certificate
        fields = ('tenant', 'active')

    def clean(self):
        from cryptography.hazmat.primitives.serialization import pkcs12 as crypto_pkcs12

        cleaned  = super().clean()
        is_new   = self.instance.pk is None
        has_file = bool(cleaned.get('p12_file'))
        has_pass = bool(cleaned.get('cert_password'))

        if is_new and not has_file:
            self.add_error('p12_file', 'El archivo .p12 es obligatorio al crear el certificado.')
        if is_new and not has_pass:
            self.add_error('cert_password', 'La contraseña es obligatoria al crear el certificado.')
        if has_file and not has_pass:
            self.add_error('cert_password', 'Ingresa la contraseña del archivo .p12.')

        if has_file and has_pass:
            p12_file  = cleaned['p12_file']
            p12_bytes = p12_file.read()
            p12_file.seek(0)
            try:
                pfx  = crypto_pkcs12.load_pkcs12(p12_bytes, cleaned['cert_password'].encode())
                cert = pfx.cert.certificate
                self._cert_expiry = cert.not_valid_after_utc.date()
            except Exception:
                self.add_error('cert_password', 'Contraseña incorrecta o archivo .p12 inválido.')

        return cleaned

    def save(self, commit=True):
        instance      = super().save(commit=False)
        p12_file      = self.cleaned_data.get('p12_file')
        cert_password = self.cleaned_data.get('cert_password')

        if p12_file:
            p12_bytes                  = p12_file.read()
            instance.p12_encrypted     = encrypt(p12_bytes).encode()
            instance.password_encrypted = encrypt(cert_password.encode())
            instance.expiry_date       = self._cert_expiry

        if commit:
            instance.save()
        return instance


# ---------------------------------------------------------------------------
# Inlines
# ---------------------------------------------------------------------------

class FiscalConfigInline(admin.StackedInline):
    model              = FiscalConfig
    form               = FiscalConfigForm
    extra              = 0
    can_delete         = False
    autocomplete_fields = ['fiscal_municipality', 'registered_municipality']

    fieldsets = (
        ('Identidad del emisor', {
            'fields': (
                ('legal_name', 'trade_name'),
                ('nit', 'check_digit'),
            ),
        }),
        ('Dirección fiscal', {
            'fields': (
                'fiscal_municipality',
                'fiscal_address',
                ('postal_code', 'country_code'),
            ),
            'description': 'Selecciona el municipio — ciudad y departamento se llenan automáticamente.',
        }),
        ('Dirección registrada DIAN', {
            'fields': (
                'registered_municipality',
                'address',
            ),
            'classes': ('collapse',),
            'description': 'Solo si difiere de la dirección fiscal.',
        }),
        ('Clasificación tributaria', {
            'fields': (
                ('person_type',),
                ('tax_responsibilities',),
                ('tax_scheme_id',),
            ),
        }),
        ('Resolución DIAN (PRODUCCIÓN)', {
            'fields': (
                'invoice_prefix',
                'resolution_number',
                ('resolution_date', 'resolution_end_date'),
                ('range_start', 'range_end', 'current_number'),
                'test_current_number',
            ),
            'description': (
                'Estos campos se usan cuando <code>ambiente = PRODUCCIÓN</code>. '
                'En PRUEBAS se usa la resolución global (Admin → Resolución de pruebas) '
                'y el contador <code>test_current_number</code>. '
                'Usa el botón "📋 Consultar resolución DIAN" para cargar estos campos '
                'automáticamente desde DIAN.'
            ),
        }),
        ('Software DIAN', {
            'fields': (
                'ambiente',
                'software_id',
                'software_pin',
                'clave_tecnica',
                'test_set_id',
            ),
            'classes': ('collapse',),
        }),
        ('Contacto', {
            'fields': (('phone', 'email'),),
        }),
    )


class CertificateInline(admin.TabularInline):
    model            = Certificate
    form             = CertificateForm
    extra            = 0
    readonly_fields  = ('created_at', 'expiry_date', 'cert_status')
    fields           = ('p12_file', 'cert_password', 'active', 'cert_status', 'expiry_date', 'created_at')
    verbose_name     = 'Certificado digital (.p12)'
    verbose_name_plural = 'Certificados digitales (.p12)'

    def cert_status(self, obj):
        if obj.pk and obj.p12_encrypted:
            return mark_safe('<span style="color:green">✔ Cargado</span>')
        return mark_safe('<span style="color:gray">— Sin certificado</span>')
    cert_status.short_description = 'Estado'


# ---------------------------------------------------------------------------
# TenantAdmin
# ---------------------------------------------------------------------------

@admin.register(Tenant)
class TenantAdmin(admin.ModelAdmin):
    list_display    = ('name', 'active', 'created_at', 'api_key_button',
                       'test_invoice_button', 'resolution_button')
    list_filter     = ('active',)
    search_fields   = ('name',)
    readonly_fields = ('id', 'api_key_hash', 'created_at')
    inlines         = [FiscalConfigInline, CertificateInline]
    save_on_top     = True

    fieldsets = (
        (None, {
            'fields': (('name', 'active'), 'id', 'api_key_hash', 'created_at'),
        }),
    )

    def api_key_button(self, obj):
        url = reverse('admin:tenant-generate-key', args=[obj.pk])
        return format_html(
            '<a class="button" href="{}">🔑 Generar nueva key</a>', url
        )
    api_key_button.short_description = 'API Key'

    def test_invoice_button(self, obj):
        url = reverse('admin:tenant-test-invoice', args=[obj.pk])
        return format_html(
            '<a class="button" href="{}">🧪 Factura prueba + Set DIAN</a>', url
        )
    test_invoice_button.short_description = 'Set de pruebas'

    def resolution_button(self, obj):
        url = reverse('admin:tenant-resolution', args=[obj.pk])
        return format_html(
            '<a class="button" href="{}">📋 Consultar resolución DIAN</a>', url
        )
    resolution_button.short_description = 'Resolución'

    def get_urls(self):
        urls   = super().get_urls()
        custom = [
            path(
                '<uuid:pk>/generate-key/',
                self.admin_site.admin_view(self._generate_key_view),
                name='tenant-generate-key',
            ),
            path(
                '<uuid:pk>/test-invoice/',
                self.admin_site.admin_view(self._test_invoice_view),
                name='tenant-test-invoice',
            ),
            path(
                '<uuid:pk>/resolution/',
                self.admin_site.admin_view(self._resolution_view),
                name='tenant-resolution',
            ),
            path(
                '<uuid:pk>/load-prod-resolution/',
                self.admin_site.admin_view(self._load_prod_resolution),
                name='tenant-load-prod-resolution',
            ),
        ]
        return custom + urls

    def _generate_key_view(self, request, pk):
        tenant          = Tenant.objects.get(pk=pk)
        raw_key, hashed = Tenant.generate_api_key()
        tenant.api_key_hash = hashed
        tenant.save(update_fields=['api_key_hash'])
        self.message_user(
            request,
            format_html(
                '<strong>API key para {}:</strong> '
                '<code style="background:#f0f0f0;padding:2px 6px">{}</code> '
                '— Guárdala ahora, no se vuelve a mostrar.',
                tenant.name,
                raw_key,
            ),
            level=messages.SUCCESS,
        )
        return HttpResponseRedirect(
            reverse('admin:tenants_tenant_change', args=[pk])
        )

    def _test_invoice_view(self, request, pk):
        """Crea factura de 1000 pesos + firma + envía al set de pruebas DIAN."""
        from django.utils import timezone
        from apps.invoices.models import Invoice
        from apps.invoices.services.invoice_service import get_next_number
        from core.cert_service import load_certificate
        from core.xml_builder import build_xml
        from core.signer import sign_xml
        from core.dian_client import send_to_test_set

        tenant = Tenant.objects.get(pk=pk)
        redirect = HttpResponseRedirect(
            reverse('admin:tenants_tenant_change', args=[pk])
        )

        try:
            config = FiscalConfig.objects.get(tenant=tenant)
        except FiscalConfig.DoesNotExist:
            self.message_user(request, 'El tenant no tiene FiscalConfig.',
                              level=messages.ERROR)
            return redirect

        if not config.test_set_id:
            self.message_user(
                request,
                'Configura primero el test_set_id en "Software DIAN" del FiscalConfig.',
                level=messages.ERROR,
            )
            return redirect

        try:
            cert = Certificate.objects.get(tenant=tenant, active=True)
        except Certificate.DoesNotExist:
            self.message_user(request, 'El tenant no tiene certificado activo.',
                              level=messages.ERROR)
            return redirect

        full_number, number, prefix = get_next_number(tenant)
        invoice = Invoice.objects.create(
            tenant=tenant,
            certificate=cert,
            prefix=prefix,
            number=number,
            full_number=full_number,
            invoice_date=timezone.now().date(),
            customer={
                'person_type':     '2',
                'document_type':   '13',
                'document_number': '222222222222',
                'legal_name':      'Consumidor Final',
                'address':         'Calle 1 # 1-1',
                'tax_level_code':  'R-99-PN',
                'tax_scheme_id':   'ZZ',
                'tax_scheme_name': 'No aplica',
                'email':           '',
                'phone':           '',
            },
            items=[{
                'description': 'Producto de prueba',
                'quantity':    1,
                'unit_price':  1000,
                'discount':    0,
                'taxes': [{'tax_type': '01', 'rate': 19}],
            }],
            subtotal=1000,
            discounts=0,
            taxes=190,
            total=1190,
            payment_means_code='10',
            external_reference='TEST-SET',
        )

        try:
            from apps.tenants.models import apply_test_resolution
            apply_test_resolution(config)
            p12, password = load_certificate(tenant)
            xml, cufe, qr_data = build_xml(invoice, config)
            signed_xml = sign_xml(xml, p12, password)

            invoice.signed_xml = signed_xml
            invoice.cufe       = cufe
            invoice.qr_data    = qr_data
            invoice.save(update_fields=['signed_xml', 'cufe', 'qr_data'])

            result = send_to_test_set(
                signed_xml, p12, password, config, config.test_set_id,
            )
        except Exception as exc:
            invoice.status = Invoice.Status.ERROR
            invoice.save(update_fields=['status'])
            self.message_user(
                request,
                f'Falló al construir/firmar la factura {full_number}: {exc}',
                level=messages.ERROR,
            )
            return redirect

        invoice.dian_response = result
        if result.get('code') == '00':
            invoice.status = Invoice.Status.SENT
            invoice.save(update_fields=['status', 'dian_response'])
            self.message_user(
                request,
                format_html(
                    'Factura <strong>{}</strong> enviada al set DIAN. '
                    '<strong>ZipKey:</strong> <code>{}</code>',
                    full_number, result.get('zip_key', ''),
                ),
                level=messages.SUCCESS,
            )
        else:
            invoice.status = Invoice.Status.REJECTED
            invoice.save(update_fields=['status', 'dian_response'])
            errs = '; '.join(result.get('errors', [])) or result.get('status_msg', 'Sin detalle')
            self.message_user(
                request,
                f'Factura {full_number} rechazada por el set DIAN: {errs}',
                level=messages.ERROR,
            )

        return redirect

    def _resolution_view(self, request, pk):
        """Consulta DIAN GetNumberingRange y muestra las resoluciones autorizadas."""
        from django.shortcuts import render
        from core.cert_service import load_certificate
        from core.dian_client import get_numbering_range

        tenant = Tenant.objects.get(pk=pk)
        redirect = HttpResponseRedirect(
            reverse('admin:tenants_tenant_change', args=[pk])
        )

        try:
            config = FiscalConfig.objects.get(tenant=tenant)
        except FiscalConfig.DoesNotExist:
            self.message_user(request, 'El tenant no tiene FiscalConfig.',
                              level=messages.ERROR)
            return redirect

        if not config.software_id:
            self.message_user(
                request,
                'Falta software_id en FiscalConfig → Software DIAN.',
                level=messages.ERROR,
            )
            return redirect

        try:
            p12, password = load_certificate(tenant)
        except Certificate.DoesNotExist:
            self.message_user(request, 'El tenant no tiene certificado activo.',
                              level=messages.ERROR)
            return redirect

        try:
            resolutions, raw = get_numbering_range(p12, password, config)
        except Exception as exc:
            self.message_user(
                request, f'Error consultando DIAN: {exc}',
                level=messages.ERROR,
            )
            return redirect

        context = {
            **self.admin_site.each_context(request),
            'tenant':       tenant,
            'config':       config,
            'resolutions':  resolutions,
            'raw_response': raw if not resolutions else '',
            'title':        f'Resoluciones DIAN — {tenant.name}',
        }
        return render(request, 'admin/tenants/tenant/resolution.html', context)

    def _load_prod_resolution(self, request, pk):
        """Recibe POST con los datos de una resolución de DIAN y los carga en
        los campos de producción del FiscalConfig."""
        if request.method != 'POST':
            return HttpResponseRedirect(
                reverse('admin:tenant-resolution', args=[pk])
            )

        from datetime import datetime as dt

        tenant = Tenant.objects.get(pk=pk)
        try:
            config = FiscalConfig.objects.get(tenant=tenant)
        except FiscalConfig.DoesNotExist:
            self.message_user(request, 'El tenant no tiene FiscalConfig.',
                              level=messages.ERROR)
            return HttpResponseRedirect(
                reverse('admin:tenant-resolution', args=[pk])
            )

        def _parse_date(s):
            return dt.strptime(s, '%Y-%m-%d').date() if s else None

        try:
            from_number = int(request.POST.get('from_number', '0') or '0')
            to_number   = int(request.POST.get('to_number',   '0') or '0')

            config.invoice_prefix      = request.POST.get('prefix', '') or ''
            config.resolution_number   = request.POST.get('resolution_number', '')
            config.resolution_date     = _parse_date(request.POST.get('resolution_date', '')) or config.resolution_date
            config.resolution_end_date = _parse_date(request.POST.get('valid_date_to', ''))
            config.range_start         = from_number
            config.range_end           = to_number
            # current_number = from_number - 1 → la primera factura será from_number
            config.current_number      = max(from_number - 1, 0)
            config.clave_tecnica       = request.POST.get('technical_key', '')
            config.save()
        except Exception as exc:
            self.message_user(
                request, f'Error guardando la resolución: {exc}',
                level=messages.ERROR,
            )
            return HttpResponseRedirect(
                reverse('admin:tenant-resolution', args=[pk])
            )

        self.message_user(
            request,
            format_html(
                'Resolución <strong>{}</strong> cargada como producción para <strong>{}</strong>. '
                'Recuerda cambiar <code>ambiente</code> a PRODUCCIÓN cuando estés listo.',
                config.resolution_number, tenant.name,
            ),
            level=messages.SUCCESS,
        )
        return HttpResponseRedirect(
            reverse('admin:tenants_tenant_change', args=[pk])
        )


# ---------------------------------------------------------------------------
# CertificateAdmin (vista independiente)
# ---------------------------------------------------------------------------

@admin.register(Certificate)
class CertificateAdmin(admin.ModelAdmin):
    form            = CertificateForm
    list_display    = ('tenant', 'expiry_date', 'active', 'cert_loaded', 'created_at')
    list_filter     = ('active', 'tenant')
    search_fields   = ('tenant__name',)
    readonly_fields = ('created_at', 'expiry_date', 'cert_loaded')

    fieldsets = (
        (None, {
            'fields': ('tenant', 'active'),
        }),
        ('Certificado digital', {
            'fields': ('p12_file', 'cert_password', 'cert_loaded', 'expiry_date'),
            'description': (
                'Sube el archivo .p12 junto con su contraseña. '
                'La fecha de vencimiento se lee automáticamente del certificado. '
                'El archivo se cifra con AES-256 antes de guardarse.'
            ),
        }),
        ('Auditoría', {
            'fields': ('created_at',),
            'classes': ('collapse',),
        }),
    )

    def cert_loaded(self, obj):
        if obj.pk and obj.p12_encrypted:
            return mark_safe('<span style="color:green;font-weight:bold">✔ Certificado cargado</span>')
        return mark_safe('<span style="color:#999">Sin certificado</span>')
    cert_loaded.short_description = 'Estado'


# ---------------------------------------------------------------------------
# TestResolution — singleton global compartido por todos los tenants en PRUEBAS
# ---------------------------------------------------------------------------

@admin.register(TestResolution)
class TestResolutionAdmin(admin.ModelAdmin):
    fieldsets = (
        ('Resolución de pruebas DIAN (global)', {
            'fields': (
                'invoice_prefix',
                'resolution_number',
                ('resolution_date', 'resolution_end_date'),
                ('range_start', 'range_end'),
                'clave_tecnica',
            ),
            'description': (
                'Esta resolución se usa por <strong>todos los tenants</strong> '
                'cuyo ambiente sea PRUEBAS. '
                'Se mantiene una única fila (singleton).'
            ),
        }),
    )
    readonly_fields = ()

    def has_add_permission(self, request):
        return not TestResolution.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False

    def changelist_view(self, request, extra_context=None):
        obj = TestResolution.get_solo()
        return HttpResponseRedirect(
            reverse('admin:tenants_testresolution_change', args=[obj.pk])
        )
