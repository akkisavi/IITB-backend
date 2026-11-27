"""Simulated shuttle battery.

Applied lazily whenever the shuttle is read, from the time elapsed since its last update, so it
needs no background worker and survives free-tier sleep. The change goes through Shuttle.save(),
so the low-battery alert fires exactly as it does for a manual update.

Busy drains fastest, idle drains slowly, charging fills up and switches to idle at 100%, offline
is frozen. A working shuttle that falls to DOCK_AT switches itself to charging (returns to dock).
"""
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import Shuttle

SECONDS_PER_PERCENT = {"busy": 20, "idle": 90, "charging": 6}
DOCK_AT = 10
MAX_STEPS = 500


def _step(status, battery):
    """One percent of change. Returns the new (status, battery)."""
    if status == "charging":
        battery = min(100, battery + 1)
        return ("idle" if battery == 100 else status), battery
    battery = max(0, battery - 1)
    if battery <= DOCK_AT:
        status = "charging"
    return status, battery


def simulate(shuttle, now=None):
    """Advance the shuttle's battery to `now`. Returns the (possibly updated) shuttle."""
    if not getattr(settings, "SHUTTLE_SIMULATION", True):
        return shuttle
    now = now or timezone.now()
    with transaction.atomic():
        current = Shuttle.objects.select_for_update().get(pk=shuttle.pk)
        status, battery = current.status, current.battery_percent
        elapsed = (now - current.updated_at).total_seconds()
        steps = 0
        while steps < MAX_STEPS:
            period = SECONDS_PER_PERCENT.get(status)
            if period is None or elapsed < period:
                break
            if status == "charging" and battery >= 100:
                status = "idle"
                break
            elapsed -= period
            status, battery = _step(status, battery)
            steps += 1
        if (status, battery) == (current.status, current.battery_percent):
            return current
        current.status, current.battery_percent = status, battery
        current.save(update_fields=["status", "battery_percent", "updated_at"])
        return current
