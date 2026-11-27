from django.db import migrations


def seed(apps, schema_editor):
    from warehouse.seeding import ensure_locations_and_shuttle

    ensure_locations_and_shuttle(apps.get_model("warehouse", "Location"), apps.get_model("warehouse", "Shuttle"))


class Migration(migrations.Migration):
    dependencies = [("warehouse", "0001_initial")]

    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
