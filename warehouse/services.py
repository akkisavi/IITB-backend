"""Stock operations. Every write runs in one transaction with the destination/source row locked."""
from django.db import IntegrityError, transaction
from django.utils import timezone

from .exceptions import CONFLICT_MESSAGE, ApiError
from .models import Location, OperationLog, Pallet
from .serializers import validate_load, validate_unload

def live_occupied_count():
    return Pallet.objects.filter(status=Pallet.STATUS_ACTIVE).count()


def _map_integrity_error(exc):
    msg = str(exc).lower()
    if "pallet_number" in msg:
        return ApiError(409, "duplicate_pallet_number", "An active pallet with this pallet number already exists.")
    if "barcode" in msg:
        return ApiError(409, "duplicate_barcode", "An active pallet with this barcode already exists.")
    if "location_id" in msg:
        return ApiError(409, "location_occupied", "This location is already occupied.")
    return ApiError(409, "conflict", CONFLICT_MESSAGE)


def _log(op_type, pallet, location_code, user, reverses=None):
    return OperationLog.objects.create(
        reverses=reverses,
        op_type=op_type,
        pallet=pallet,
        location_code=location_code,
        pallet_number=pallet.pallet_number,
        item_name=pallet.item_name,
        quantity=pallet.quantity,
        user=user,
        username=user.username if user else "",
    )


def load_pallet(data, user):
    clean = validate_load(data)
    code = clean["location"].code
    try:
        with transaction.atomic():
            loc = Location.objects.select_for_update().get(code=code)
            occupant = Pallet.objects.filter(location=loc, status=Pallet.STATUS_ACTIVE).first()
            if occupant:
                raise ApiError(
                    409, "location_occupied", f"Location {code} is already occupied by pallet {occupant.pallet_number}."
                )
            dup = (
                Pallet.objects.select_related("location")
                .filter(status=Pallet.STATUS_ACTIVE, pallet_number=clean["pallet_number"])
                .first()
            )
            if dup:
                raise ApiError(
                    409,
                    "duplicate_pallet_number",
                    f"Pallet {dup.pallet_number} is already in the warehouse at {dup.location.code}.",
                )
            dup = (
                Pallet.objects.select_related("location")
                .filter(status=Pallet.STATUS_ACTIVE, barcode=clean["barcode"])
                .first()
            )
            if dup:
                raise ApiError(
                    409,
                    "duplicate_barcode",
                    f"Barcode {dup.barcode} is already in use by pallet {dup.pallet_number} at {dup.location.code}.",
                )
            pallet = Pallet.objects.create(
                pallet_number=clean["pallet_number"],
                barcode=clean["barcode"],
                item_name=clean["item_name"],
                quantity=clean["quantity"],
                location=loc,
                storage_location=code,
                status=Pallet.STATUS_ACTIVE,
                loaded_by=user,
            )
            op = _log(OperationLog.LOAD, pallet, code, user)
    except IntegrityError as exc:
        raise _map_integrity_error(exc) from exc
    return op, pallet, loc


def unload_pallet(data, user):
    code = validate_unload(data).code
    try:
        with transaction.atomic():
            loc = Location.objects.select_for_update().get(code=code)
            pallet = (
                Pallet.objects.select_for_update()
                .filter(location=loc, status=Pallet.STATUS_ACTIVE)
                .first()
            )
            if pallet is None:
                raise ApiError(409, "location_empty", f"Location {code} is already empty.")
            _deactivate(pallet, user)
            op = _log(OperationLog.UNLOAD, pallet, code, user)
    except IntegrityError as exc:
        raise ApiError(409, "conflict", CONFLICT_MESSAGE) from exc
    return op, pallet, loc


def _deactivate(pallet, user):
    pallet.status = Pallet.STATUS_UNLOADED
    pallet.location = None
    pallet.unloaded_at = timezone.now()
    pallet.unloaded_by = user
    pallet.save(update_fields=["status", "location", "unloaded_at", "unloaded_by"])


def latest_operation(pallet_id):
    return OperationLog.objects.filter(pallet_id=pallet_id).order_by("-created_at", "-id").first()


def undo_operation(op_id, user):
    """Reverse the most recent movement of a pallet by recording the opposite movement.

    Idempotent: if `op_id` was already reversed, the existing reversal is returned with
    created=False. The one-to-one `reverses` column makes a concurrent double undo impossible.
    Returns (reversal_op, pallet, created).
    """
    try:
        with transaction.atomic():
            op = OperationLog.objects.select_for_update().filter(pk=op_id).first()
            if op is None:
                raise ApiError(404, "not_found", "Operation not found.")
            existing = OperationLog.objects.filter(reverses=op).select_related("pallet").first()
            if existing:
                return existing, existing.pallet, False
            if op.reverses_id:
                raise ApiError(409, "cannot_undo_undo", "This operation is itself an undo and cannot be undone.")
            pallet = Pallet.objects.select_for_update().get(pk=op.pallet_id)
            if latest_operation(pallet.pk).pk != op.pk:
                raise ApiError(
                    409, "not_latest", "Only the most recent operation on a pallet can be undone. Refresh and retry."
                )
            loc = Location.objects.select_for_update().get(code=op.location_code)
            if op.op_type == OperationLog.LOAD:
                if pallet.status != Pallet.STATUS_ACTIVE or pallet.location_id != loc.id:
                    raise ApiError(409, "conflict", CONFLICT_MESSAGE)
                _deactivate(pallet, user)
                reversal = _log(OperationLog.UNLOAD, pallet, loc.code, user, reverses=op)
            else:
                occupant = Pallet.objects.filter(location=loc, status=Pallet.STATUS_ACTIVE).first()
                if occupant:
                    raise ApiError(
                        409, "location_occupied",
                        f"Cannot undo: {loc.code} is now occupied by pallet {occupant.pallet_number}.",
                    )
                active = Pallet.objects.filter(status=Pallet.STATUS_ACTIVE)
                if active.filter(pallet_number=pallet.pallet_number).exists():
                    raise ApiError(409, "duplicate_pallet_number",
                                   f"Cannot undo: pallet {pallet.pallet_number} is in stock again.")
                if active.filter(barcode=pallet.barcode).exists():
                    raise ApiError(409, "duplicate_barcode", f"Cannot undo: barcode {pallet.barcode} is in stock again.")
                pallet.status = Pallet.STATUS_ACTIVE
                pallet.location = loc
                pallet.unloaded_at = None
                pallet.unloaded_by = None
                pallet.save(update_fields=["status", "location", "unloaded_at", "unloaded_by"])
                reversal = _log(OperationLog.LOAD, pallet, loc.code, user, reverses=op)
    except IntegrityError as exc:
        existing = OperationLog.objects.filter(reverses_id=op_id).select_related("pallet").first()
        if existing:
            return existing, existing.pallet, False
        raise _map_integrity_error(exc) from exc
    return reversal, pallet, True


def bulk_unload(codes, user):
    """Unload each location in its own transaction so one failure does not roll back the rest."""
    results = []
    for code in codes:
        try:
            op, pallet, _ = unload_pallet({"location": code}, user)
            results.append({"location": code, "ok": True, "pallet_number": pallet.pallet_number,
                            "operation_id": op.id})
        except ApiError as exc:
            detail = exc.detail
            if exc.errors:
                detail = " ".join(m for msgs in exc.errors.values() for m in msgs)
            results.append({"location": code, "ok": False, "code": exc.api_code, "detail": str(detail)})
    return results
