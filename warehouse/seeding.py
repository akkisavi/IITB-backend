"""Shared seed helpers (used by the data migration's logic, seed_demo and reset_demo)."""
COLUMNS = "ABCDE"
ROWS = range(1, 6)
SHUTTLE_ID = "SH-01"
SHUTTLE_DEFAULTS = {"status": "idle", "battery_percent": 87}


def location_specs():
    for row in ROWS:
        for col in COLUMNS:
            code = f"{col}{row}"
            yield {"code": code, "column": col, "row": row, "kind": "transfer" if code == "A1" else "storage"}


def ensure_locations_and_shuttle(Location, Shuttle):
    created = 0
    for spec in location_specs():
        _, was_created = Location.objects.get_or_create(code=spec["code"], defaults=spec)
        created += int(was_created)
    _, shuttle_created = Shuttle.objects.get_or_create(identifier=SHUTTLE_ID, defaults=SHUTTLE_DEFAULTS)
    return created, shuttle_created
