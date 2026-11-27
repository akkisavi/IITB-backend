"""Destructive reset of warehouse data (not users) on an explicitly selected DB (SPEC 8.3)."""

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from warehouse.models import IdempotencyRecord, OperationLog, Pallet, Shuttle, ShuttleMaintenance
from warehouse.seeding import SHUTTLE_DEFAULTS, SHUTTLE_ID


class Command(BaseCommand):
    help = "DESTRUCTIVE: delete all pallets, movements and shuttle notes, reset shuttle, then run seed_demo."

    def add_arguments(self, parser):
        parser.add_argument("--yes-i-am-sure", action="store_true", dest="confirm")
        parser.add_argument("--allow-remote", action="store_true", help="Permit reset on a hosted deploy (DJANGO_ALLOWED_HOSTS not local).")

    def handle(self, *args, **options):
        db = settings.DATABASES["default"]
        target = f"{db['ENGINE'].rsplit('.', 1)[-1]}: {db.get('NAME')}"
        if db.get("HOST"):
            target += f" @ {db['HOST']}"
        self.stdout.write(f"Target database: {target}")
        if not options["confirm"]:
            raise CommandError("Refusing to reset: pass --yes-i-am-sure to delete all warehouse data.", returncode=1)
        if not settings.LOCAL_MODE and not options["allow_remote"]:
            raise CommandError("Refusing to reset a hosted deploy. Pass --allow-remote as well.", returncode=1)
        with transaction.atomic():
            IdempotencyRecord.objects.all().delete()
            ShuttleMaintenance.objects.all().delete()
            ops = OperationLog.objects.all().delete()[0]
            pallets = Pallet.objects.all().delete()[0]
            Shuttle.objects.update_or_create(identifier=SHUTTLE_ID, defaults=SHUTTLE_DEFAULTS)
        self.stdout.write(f"Deleted {ops} operation logs and {pallets} pallets; shuttle reset.")
        call_command("seed_demo", stdout=self.stdout, stderr=self.stderr)
