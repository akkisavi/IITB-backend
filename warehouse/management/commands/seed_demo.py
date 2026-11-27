"""Idempotent, non-destructive demo seed (SPEC 8.2). Never prints passwords."""
import os
import re
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction

from accounts.models import UserProfile
from warehouse.models import Location, OperationLog, Pallet, Shuttle
from warehouse.seeding import ensure_locations_and_shuttle
from warehouse.services import live_occupied_count

ROLES = ("admin", "auditor", "technician")
SAMPLE_PALLETS = [
    ("PLT-1001", "40000000000017", "Steel bolts M8", 500, "B1"),
    ("PLT-1002", "40000000000024", "Copper wire", 120, "D2"),
    ("PLT-1003", "40000000000031", "Cardboard boxes", 300, "C3"),
    ("PLT-1004", "40000000000048", "Paint cans", 64, "E4"),
    ("PLT-1005", "40000000000055", "Steel bolts M8", 400, "B5"),
]


class Command(BaseCommand):
    help = "Create/update demo accounts from EVAL_* env vars and seed sample warehouse data (idempotent)."

    def handle(self, *args, **options):
        User = get_user_model()
        accounts = self._seed_accounts(User)

        loc_created, shuttle_created = ensure_locations_and_shuttle(Location, Shuttle)
        self.stdout.write(
            f"Locations: {Location.objects.count()} present ({loc_created} created); "
            f"shuttle SH-01 {'created' if shuttle_created else 'already present'}."
        )

        admin_user = accounts.get("admin")
        if admin_user is None:
            admin_name = (os.environ.get("EVAL_ADMIN_USERNAME") or "").strip() or "demo_admin"
            admin_user = User.objects.filter(username=admin_name).first()
        self._seed_pallets(admin_user)
        self.stdout.write(f"Live stock: {live_occupied_count()} occupied. Run seed_history for a 7-day history.")
        self.stdout.write(self.style.SUCCESS("seed_demo complete."))

    def _seed_accounts(self, User):
        result = {}
        for role in ROLES:
            prefix = f"EVAL_{role.upper()}"
            username = (os.environ.get(f"{prefix}_USERNAME") or "").strip() or f"demo_{role}"
            password = os.environ.get(f"{prefix}_PASSWORD") or ""
            if not password:
                self.stdout.write(self.style.WARNING(f"Skipping {username}: {prefix}_PASSWORD not set."))
                continue
            if not (12 <= len(password) <= 128) or re.search(r"\s", password):
                self.stdout.write(
                    self.style.WARNING(
                        f"Warning: {prefix}_PASSWORD does not meet the 12-128 characters, no-whitespace policy "
                        f"(account {username} is still created)."
                    )
                )
            with transaction.atomic():
                user, created = User.objects.get_or_create(username=username)
                user.set_password(password)
                user.is_active = True
                user.save()
                UserProfile.objects.update_or_create(user=user, defaults={"role": role})
            result[role] = user
            self.stdout.write(f"Account {username} ({role}) {'created' if created else 'updated'}.")
        return result

    def _seed_pallets(self, admin_user):
        if Pallet.objects.filter(status=Pallet.STATUS_ACTIVE).exists() or OperationLog.objects.exists():
            self.stdout.write("Sample pallets: skipped (warehouse already has stock or history).")
            return
        username = admin_user.username if admin_user else ""
        with transaction.atomic():
            for number, barcode, item, qty, code in SAMPLE_PALLETS:
                loc = Location.objects.get(code=code)
                pallet = Pallet.objects.create(
                    pallet_number=number, barcode=barcode, item_name=item, quantity=qty,
                    location=loc, storage_location=loc.code, status=Pallet.STATUS_ACTIVE, loaded_by=admin_user,
                )
                OperationLog.objects.create(
                    op_type=OperationLog.LOAD, pallet=pallet, location_code=code, pallet_number=number,
                    item_name=item, quantity=qty, user=admin_user, username=username,
                )
        self.stdout.write(f"Sample pallets: created {len(SAMPLE_PALLETS)}.")
