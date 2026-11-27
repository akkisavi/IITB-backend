"""Response shapes and request validation. The validate_* functions are the source of truth for
input rules; the UI mirrors them in src/lib/validate.ts."""
import re
from datetime import timezone as dt_timezone

from django.conf import settings
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.validators import validate_email
from rest_framework import serializers

from .exceptions import field_errors
from .models import MAX_QUANTITY, Location, Shuttle, ShuttleMaintenance

PALLET_RE = re.compile(r"^[A-Z0-9-]{3,32}$")
BARCODE_RE = re.compile(r"^[0-9]{8,14}$")
DIGITS_RE = re.compile(r"^[0-9]+$")

MSG_PALLET = "Pallet number must be 3-32 characters: letters, digits or hyphen."
MSG_BARCODE = "Barcode must be 8-14 digits."
MSG_ITEM_REQUIRED = "Item name is required."
MSG_ITEM_LONG = "Item name must be at most 100 characters."
MSG_QTY = "Quantity must be a whole number between 1 and 100000."
MSG_UNKNOWN_LOCATION = "Unknown location."
MSG_A1_LOAD = "A1 is the transfer point and cannot store pallets."
MSG_A1_UNLOAD = "A1 is the transfer point and holds no stock."


def _str(value):
    return value.strip() if isinstance(value, str) else None


def _parse_quantity(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        qty = value
    elif isinstance(value, str) and DIGITS_RE.match(value.strip()):
        qty = int(value.strip())
    else:  # floats (incl. 12.0), decimals, None, other types
        return None
    return qty if 1 <= qty <= MAX_QUANTITY else None


def _resolve_location(raw, errors, a1_message):
    code = _str(raw)
    code = code.upper() if code else ""
    loc = Location.objects.filter(code=code).first() if code else None
    if loc is None:
        errors["location"] = [MSG_UNKNOWN_LOCATION]
    elif loc.kind == Location.KIND_TRANSFER:
        errors["location"] = [a1_message]
    return loc


def validate_load(data):
    if not isinstance(data, dict):
        field_errors({"non_field_errors": ["Expected a JSON object."]})
    errors, clean = {}, {}

    pn = _str(data.get("pallet_number"))
    pn = pn.upper() if pn else ""
    if not PALLET_RE.match(pn):
        errors["pallet_number"] = [MSG_PALLET]
    clean["pallet_number"] = pn

    bc = _str(data.get("barcode")) or ""
    if not BARCODE_RE.match(bc):
        errors["barcode"] = [MSG_BARCODE]
    clean["barcode"] = bc

    item = _str(data.get("item_name"))
    item = " ".join(item.split()) if item else ""
    if not item:
        errors["item_name"] = [MSG_ITEM_REQUIRED]
    elif len(item) > 100:
        errors["item_name"] = [MSG_ITEM_LONG]
    clean["item_name"] = item

    qty = _parse_quantity(data.get("quantity"))
    if qty is None:
        errors["quantity"] = [MSG_QTY]
    clean["quantity"] = qty

    clean["location"] = _resolve_location(data.get("location"), errors, MSG_A1_LOAD)
    if errors:
        field_errors(errors)
    return clean


def validate_unload(data):
    if not isinstance(data, dict):
        field_errors({"non_field_errors": ["Expected a JSON object."]})
    errors = {}
    loc = _resolve_location(data.get("location"), errors, MSG_A1_UNLOAD)
    if errors:
        field_errors(errors)
    return loc



_dt = serializers.DateTimeField(default_timezone=dt_timezone.utc)


def iso(value):
    return _dt.to_representation(value) if value else None


def pallet_data(p):
    return {
        "id": p.id,
        "pallet_number": p.pallet_number,
        "barcode": p.barcode,
        "item_name": p.item_name,
        "quantity": p.quantity,
        "status": p.status,
        "location": p.location.code if p.location_id else None,
        "storage_location": p.storage_location or None,
        "loaded_at": iso(p.loaded_at),
        "loaded_by": p.loaded_by.username if p.loaded_by_id else None,
        "unloaded_at": iso(p.unloaded_at),
        "unloaded_by": p.unloaded_by.username if p.unloaded_by_id else None,
    }


def location_data(loc, pallet=None):
    if loc.kind == Location.KIND_TRANSFER:
        state = "transfer"
    else:
        state = "occupied" if pallet is not None else "free"
    return {
        "code": loc.code,
        "column": loc.column,
        "row": loc.row,
        "kind": loc.kind,
        "state": state,
        "pallet": pallet_data(pallet) if pallet is not None else None,
    }


def operation_data(op, include_pallet_id=True):
    data = {
        "id": op.id,
        "op_type": op.op_type,
        "location_code": op.location_code,
        "pallet_id": op.pallet_id,
        "pallet_number": op.pallet_number,
        "item_name": op.item_name,
        "quantity": op.quantity,
        "username": op.username,
        "created_at": iso(op.created_at),
        "reverses": op.reverses_id,
    }
    if not include_pallet_id:
        data.pop("pallet_id")
    return data


def shuttle_data(s: Shuttle):
    return {
        "identifier": s.identifier,
        "status": s.status,
        "battery_percent": s.battery_percent,
        "low_battery": s.battery_percent < Shuttle.LOW_BATTERY_THRESHOLD,
        "low_battery_threshold": Shuttle.LOW_BATTERY_THRESHOLD,
        "simulated": getattr(settings, "SHUTTLE_SIMULATION", True),
        "updated_at": iso(s.updated_at),
    }


def maintenance_data(m: ShuttleMaintenance):
    return {"id": m.id, "note": m.note, "username": m.username, "created_at": iso(m.created_at)}


MSG_NOTE = "Service note is required (at most 500 characters)."


def validate_maintenance(data):
    note = data.get("note") if isinstance(data, dict) else None
    note = note.strip() if isinstance(note, str) else ""
    if not note or len(note) > 500:
        field_errors({"note": [MSG_NOTE]})
    return note


def validate_shuttle_update(data):
    """PATCH /api/shuttle/: battery_percent (whole number 0-100) and/or status.

    Optional test_alert_email: with a battery_percent below the threshold, the low-battery alert
    for this save goes only to that address (for checking that alert email works).
    """
    if not isinstance(data, dict):
        field_errors({"non_field_errors": ["Send a JSON object."]})
    changes, errors = {}, {}
    if "battery_percent" in data:
        b = data["battery_percent"]
        if isinstance(b, bool) or not isinstance(b, int) or not 0 <= b <= 100:
            errors["battery_percent"] = ["Battery must be a whole number from 0 to 100."]
        else:
            changes["battery_percent"] = b
    if "status" in data:
        valid = [c for c, _ in Shuttle.STATUS_CHOICES]
        if data["status"] not in valid:
            errors["status"] = [f"Status must be one of: {', '.join(valid)}."]
        else:
            changes["status"] = data["status"]
    test_email = data.get("test_alert_email")
    if test_email not in (None, ""):
        test_email = test_email.strip() if isinstance(test_email, str) else ""
        try:
            validate_email(test_email)
            if len(test_email) > 254:
                raise DjangoValidationError("too long")
        except DjangoValidationError:
            errors["test_alert_email"] = ["Enter a valid email address."]
        else:
            if changes.get("battery_percent", Shuttle.LOW_BATTERY_THRESHOLD) >= Shuttle.LOW_BATTERY_THRESHOLD:
                errors["test_alert_email"] = [
                    f"A test alert needs battery_percent below {Shuttle.LOW_BATTERY_THRESHOLD}% in the same update."
                ]
            else:
                changes["test_alert_email"] = test_email
    if not errors and not changes:
        errors["non_field_errors"] = ["Provide battery_percent and/or status."]
    if errors:
        field_errors(errors)
    return changes


def validate_bulk_unload(data):
    codes = data.get("locations") if isinstance(data, dict) else None
    if not isinstance(codes, list) or not codes or len(codes) > 24 or not all(isinstance(c, str) for c in codes):
        field_errors({"locations": ["Provide a list of 1-24 location codes."]})
    return list(dict.fromkeys(c.strip().upper() for c in codes))


