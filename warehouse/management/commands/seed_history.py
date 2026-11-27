"""Generate a reproducible 7-day stream of stock movements (LOAD/UNLOAD) ending at the live map.

The chart derives every daily value from these movements, so the last point always equals the
map. The same --seed on the same IST calendar day produces the same history.
"""
import random
from datetime import datetime, time, timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from accounts.models import Role
from warehouse.models import IdempotencyRecord, Location, OperationLog, Pallet

ITEMS = ["Steel bolts M8", "Copper wire", "Cardboard boxes", "Paint cans", "PVC pipes", "Hydraulic oil",
         "Ceramic tiles", "Glass jars", "Rice bags", "LED panels"]


class Command(BaseCommand):
    help = "Replace warehouse stock and movements with a seeded, reproducible 7-day history."

    def add_arguments(self, parser):
        parser.add_argument("--seed", type=int, default=42)
        parser.add_argument("--days", type=int, default=7)
        parser.add_argument("--allow-remote", action="store_true", help="Permit running on a hosted deploy (DJANGO_ALLOWED_HOSTS not local).")

    def handle(self, *args, **options):
        if not settings.LOCAL_MODE and not options["allow_remote"]:
            raise CommandError("Refusing on a hosted deploy. Pass --allow-remote to replace hosted data.")
        days = options["days"]
        if not 1 <= days <= 60:
            raise CommandError("--days must be between 1 and 60.")
        rng = random.Random(options["seed"])
        User = get_user_model()
        actors = list(
            User.objects.filter(is_active=True, profile__role__in=[Role.TECHNICIAN, Role.ADMIN]).order_by("username")
        )
        slots = list(Location.objects.filter(kind=Location.KIND_STORAGE).order_by("code"))
        if not slots:
            raise CommandError("No storage locations. Run migrate first.")

        now = timezone.now()
        today = timezone.localdate()
        with transaction.atomic():
            IdempotencyRecord.objects.all().delete()
            OperationLog.objects.all().delete()
            Pallet.objects.all().delete()
            occupied, seq, count = {}, 0, 0
            for k in range(days - 1, -1, -1):
                day = today - timedelta(days=k)
                start = timezone.make_aware(datetime.combine(day, time(7, 0)))
                stamps = sorted(start + timedelta(minutes=rng.randint(0, 13 * 60)) for _ in range(rng.randint(4, 10)))
                for t in stamps:
                    if t >= now:
                        continue
                    user = rng.choice(actors) if actors else None
                    free = [s for s in slots if s.code not in occupied]
                    want_load = rng.random() < (0.65 if len(occupied) < 12 else 0.4)
                    if free and (want_load or not occupied):
                        seq += 1
                        loc = rng.choice(free)
                        pallet = Pallet.objects.create(
                            pallet_number=f"PLT-{2000 + seq}", barcode=f"89{seq:012d}", item_name=rng.choice(ITEMS),
                            quantity=rng.randint(10, 500), location=loc, storage_location=loc.code,
                            status=Pallet.STATUS_ACTIVE, loaded_at=t, loaded_by=user,
                        )
                        occupied[loc.code] = pallet
                        op_type = OperationLog.LOAD
                    elif occupied:
                        code = rng.choice(sorted(occupied))
                        pallet = occupied.pop(code)
                        pallet.status, pallet.location = Pallet.STATUS_UNLOADED, None
                        pallet.unloaded_at, pallet.unloaded_by = t, user
                        pallet.save(update_fields=["status", "location", "unloaded_at", "unloaded_by"])
                        loc = next(s for s in slots if s.code == code)
                        op_type = OperationLog.UNLOAD
                    else:
                        continue
                    OperationLog.objects.create(
                        op_type=op_type, pallet=pallet, location_code=loc.code, pallet_number=pallet.pallet_number,
                        item_name=pallet.item_name, quantity=pallet.quantity, user=user,
                        username=user.username if user else "seed", created_at=t,
                    )
                    count += 1
        self.stdout.write(
            self.style.SUCCESS(
                f"seed_history: {count} movements over {days} days (seed {options['seed']}); "
                f"{len(occupied)} pallets now in stock."
            )
        )
