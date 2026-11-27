from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import F, Q
from django.utils import timezone

CAPACITY = 24
MAX_QUANTITY = 100000


class Location(models.Model):
    KIND_TRANSFER = "transfer"
    KIND_STORAGE = "storage"
    KIND_CHOICES = [(KIND_TRANSFER, "Transfer"), (KIND_STORAGE, "Storage")]

    code = models.CharField(max_length=2, unique=True)
    column = models.CharField(max_length=1)
    row = models.PositiveSmallIntegerField()
    kind = models.CharField(max_length=10, choices=KIND_CHOICES, default=KIND_STORAGE)

    class Meta:
        ordering = ["row", "column"]
        constraints = [models.UniqueConstraint(fields=["column", "row"], name="uniq_location_column_row")]

    def __str__(self):
        return self.code


class Pallet(models.Model):
    STATUS_ACTIVE = "active"
    STATUS_UNLOADED = "unloaded"
    STATUS_CHOICES = [(STATUS_ACTIVE, "Active"), (STATUS_UNLOADED, "Unloaded")]

    pallet_number = models.CharField(max_length=32)
    barcode = models.CharField(max_length=14)
    item_name = models.CharField(max_length=100)
    quantity = models.PositiveIntegerField(validators=[MinValueValidator(1), MaxValueValidator(MAX_QUANTITY)])
    location = models.OneToOneField(
        Location, on_delete=models.PROTECT, null=True, blank=True, related_name="pallet"
    )
    # Kept after unload (when `location` is cleared) so history still says where the pallet was.
    storage_location = models.CharField(max_length=2, blank=True, default="")
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_ACTIVE, db_index=True)
    # Copies of pallet_number/barcode while in stock, NULL once unloaded. A plain UNIQUE index on
    # a nullable column gives "unique among in-stock pallets" (NULLs never clash). Kept in sync by save().
    active_pallet_number = models.CharField(max_length=32, null=True, blank=True, unique=True, editable=False)
    active_barcode = models.CharField(max_length=14, null=True, blank=True, unique=True, editable=False)
    loaded_at = models.DateTimeField(default=timezone.now)
    unloaded_at = models.DateTimeField(null=True, blank=True)
    loaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    unloaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    class Meta:
        ordering = ["-loaded_at", "-id"]
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(status="active", active_pallet_number=F("pallet_number"), active_barcode=F("barcode"))
                    | Q(status="unloaded", active_pallet_number__isnull=True, active_barcode__isnull=True)
                ),
                name="pallet_active_keys_consistent",
            ),
            models.CheckConstraint(
                condition=(Q(status="active") & Q(location__isnull=False))
                | (Q(status="unloaded") & Q(location__isnull=True)),
                name="pallet_status_location_consistent",
            ),
            models.CheckConstraint(
                condition=Q(quantity__gte=1) & Q(quantity__lte=MAX_QUANTITY), name="pallet_quantity_range"
            ),
        ]

    def save(self, *args, **kwargs):
        active = self.status == self.STATUS_ACTIVE
        self.active_pallet_number = self.pallet_number if active else None
        self.active_barcode = self.barcode if active else None
        update_fields = kwargs.get("update_fields")
        if update_fields is not None:
            kwargs["update_fields"] = set(update_fields) | {"active_pallet_number", "active_barcode"}
        super().save(*args, **kwargs)

    def __str__(self):
        return self.pallet_number


class OperationLog(models.Model):
    LOAD = "LOAD"
    UNLOAD = "UNLOAD"
    OP_CHOICES = [(LOAD, "Load"), (UNLOAD, "Unload")]

    op_type = models.CharField(max_length=8, choices=OP_CHOICES)
    pallet = models.ForeignKey(Pallet, on_delete=models.PROTECT, related_name="operations")
    location_code = models.CharField(max_length=2)
    pallet_number = models.CharField(max_length=32)
    item_name = models.CharField(max_length=100)
    quantity = models.PositiveIntegerField()
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    username = models.CharField(max_length=150, blank=True)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    # Set on an undo movement. One-to-one, so a given operation can be reversed at most once
    # even when the undo request is retried or sent twice concurrently.
    reverses = models.OneToOneField(
        "self", on_delete=models.PROTECT, null=True, blank=True, related_name="reversed_by"
    )

    class Meta:
        ordering = ["-created_at", "-id"]


class DailyOccupancySnapshot(models.Model):
    SOURCE_SAMPLE = "sample"
    SOURCE_LIVE = "live"
    SOURCE_CHOICES = [(SOURCE_SAMPLE, "Sample"), (SOURCE_LIVE, "Live")]

    date = models.DateField(unique=True)
    occupied = models.PositiveSmallIntegerField(validators=[MaxValueValidator(CAPACITY)])
    free = models.PositiveSmallIntegerField(validators=[MaxValueValidator(CAPACITY)])
    capacity = models.PositiveSmallIntegerField(default=CAPACITY)
    source = models.CharField(max_length=8, choices=SOURCE_CHOICES, default=SOURCE_LIVE)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["date"]


class IdempotencyRecord(models.Model):
    """Stored outcome of a successful load/unload sent with an Idempotency-Key header.

    A retry with the same key (same user, same endpoint, same body) gets the original response
    back instead of running the operation again. Failed requests are not stored, so a client
    can fix its input and retry with the same key.
    """

    key = models.CharField(max_length=64)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    endpoint = models.CharField(max_length=16)
    request_hash = models.CharField(max_length=64)
    response_status = models.PositiveSmallIntegerField()
    response_body = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["user", "key"], name="uniq_idempotency_user_key")]


class Shuttle(models.Model):
    LOW_BATTERY_THRESHOLD = 20
    STATUS_CHOICES = [("idle", "Idle"), ("busy", "Busy"), ("charging", "Charging"), ("offline", "Offline")]

    identifier = models.CharField(max_length=16, unique=True)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default="idle")
    battery_percent = models.PositiveSmallIntegerField(default=87, validators=[MaxValueValidator(100)])
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["identifier"]
        constraints = [
            models.CheckConstraint(
                condition=Q(battery_percent__gte=0) & Q(battery_percent__lte=100), name="shuttle_battery_range"
            )
        ]


class ShuttleMaintenance(models.Model):
    shuttle = models.ForeignKey(Shuttle, on_delete=models.CASCADE, related_name="maintenance")
    note = models.CharField(max_length=500)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    username = models.CharField(max_length=150, blank=True)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ["-created_at", "-id"]
