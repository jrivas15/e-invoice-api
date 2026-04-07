import csv
from pathlib import Path

from django.db import migrations

# CSV lives at project root (two levels up from apps/tenants/migrations/)
CSV_PATH = Path(__file__).resolve().parents[3] / 'MUN.csv'


def load_municipalities(apps, schema_editor):
    Municipality = apps.get_model('tenants', 'Municipality')

    seen = set()
    rows = []

    with open(CSV_PATH, encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            dept_code = str(row['Código Departamento']).strip().zfill(2)
            city_code = str(row['Código Municipio']).strip().zfill(5)
            dept_name = row['Nombre Departamento'].strip()
            city_name = row['Nombre Municipio'].strip()

            if city_code in seen:
                continue
            seen.add(city_code)

            rows.append(Municipality(
                department_code=dept_code,
                department_name=dept_name,
                city_code=city_code,
                city_name=city_name,
            ))

    Municipality.objects.bulk_create(rows)


def unload_municipalities(apps, schema_editor):
    apps.get_model('tenants', 'Municipality').objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ('tenants', '0004_municipality_model'),
    ]

    operations = [
        migrations.RunPython(load_municipalities, reverse_code=unload_municipalities),
    ]
