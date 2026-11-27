"""Add illustrative movements to an untouched demo warehouse."""

from datetime import datetime, time, timedelta

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from warehouse.models import OperationLog, Pallet


ITEMS = (
    ("Ceramic tiles", 240, "A2"),
    ("Rice bags", 180, "C1"),
    ("LED panels", 72, "D4"),
    ("PVC pipes", 300, "E3"),
    ("Glass jars", 144, "B2"),
    ("Hydraulic oil", 56, "D5"),
)


class Command(BaseCommand):
    help = "Add seven days of sample activity to an untouched demo, preserving current stock."

    def handle(self, *args, **options):
        with transaction.atomic():
            active = list(Pallet.objects.filter(status=Pallet.STATUS_ACTIVE).values_list("pallet_number", flat=True))
            baseline = {f"PLT-{number}" for number in range(1001, 1006)}
            if set(active) != baseline or len(active) != 5:
                self.stdout.write("Sample history skipped: warehouse stock has changed.")
                return
            if OperationLog.objects.count() != 5 or OperationLog.objects.exclude(
                op_type=OperationLog.LOAD, pallet_number__in=baseline
            ).exists():
                self.stdout.write("Sample history already present or warehouse activity has changed.")
                return

            today = timezone.localdate()
            actor = Pallet.objects.get(pallet_number="PLT-1001").loaded_by
            for day_offset in range(6, 0, -1):
                day = today - timedelta(days=day_offset)
                item, quantity, location = ITEMS[6 - day_offset]
                number = f"DEMO-{day.isoformat()}"
                loaded = timezone.make_aware(datetime.combine(day, time(9, 15)))
                unload_time = time(0, 1) if day_offset == 1 else time(14, 30)
                unloaded = timezone.make_aware(datetime.combine(day + timedelta(days=1), unload_time))
                pallet = Pallet.objects.create(
                    pallet_number=number,
                    barcode=f"89{day.strftime('%y%m%d')}000001",
                    item_name=item,
                    quantity=quantity,
                    status=Pallet.STATUS_UNLOADED,
                    storage_location=location,
                    loaded_at=loaded,
                    unloaded_at=unloaded,
                    loaded_by=actor,
                    unloaded_by=actor,
                )
                for kind, timestamp in ((OperationLog.LOAD, loaded), (OperationLog.UNLOAD, unloaded)):
                    OperationLog.objects.create(
                        op_type=kind,
                        pallet=pallet,
                        location_code=location,
                        pallet_number=number,
                        item_name=item,
                        quantity=quantity,
                        user=actor,
                        username=actor.username if actor else "demo",
                        created_at=timestamp,
                    )
        self.stdout.write(self.style.SUCCESS("Added 12 sample movements over seven days."))
