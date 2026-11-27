"""Occupancy figures derived from the append-only OperationLog (the stock movement table).

Day boundaries are warehouse local time (settings.TIME_ZONE, Asia/Kolkata). A day's value is the
number of occupied storage slots at the end of that day (23:59:59 IST); today's value is live
"so far" and therefore equals the map.
"""
from collections import Counter
from datetime import datetime, time, timedelta

from django.db.models import Count, Q
from django.utils import timezone

from .models import CAPACITY, Location, OperationLog, Pallet

DAYS = 7


def _day_start(d):
    return timezone.make_aware(datetime.combine(d, time.min))


def _net(qs):
    agg = qs.aggregate(
        loads=Count("id", filter=Q(op_type=OperationLog.LOAD)),
        unloads=Count("id", filter=Q(op_type=OperationLog.UNLOAD)),
    )
    return agg["loads"] - agg["unloads"]


def _clamp(n):
    return max(0, min(CAPACITY, n))


def daily_series(days=DAYS):
    """One point per day, oldest first, with end-of-day occupancy and that day's movement counts."""
    today = timezone.localdate()
    first = today - timedelta(days=days - 1)
    start = _day_start(first)
    occupied = _net(OperationLog.objects.filter(created_at__lt=start))
    per_day = {first + timedelta(days=k): {"loads": 0, "unloads": 0} for k in range(days)}
    for op_type, created in OperationLog.objects.filter(created_at__gte=start).values_list("op_type", "created_at"):
        bucket = per_day.get(timezone.localdate(created))
        if bucket is not None:
            bucket["loads" if op_type == OperationLog.LOAD else "unloads"] += 1
    points = []
    for d, counts in per_day.items():
        occupied += counts["loads"] - counts["unloads"]
        occ = _clamp(occupied)
        points.append({
            "date": d.isoformat(),
            "occupied": occ,
            "free": CAPACITY - occ,
            "loads": counts["loads"],
            "unloads": counts["unloads"],
            "source": "live_so_far" if d == today else "end_of_day",
        })
    return points


def by_column():
    totals = Counter(Location.objects.filter(kind=Location.KIND_STORAGE).values_list("column", flat=True))
    used = Counter(
        Pallet.objects.filter(status=Pallet.STATUS_ACTIVE).values_list("location__column", flat=True)
    )
    return [{"column": c, "capacity": totals[c], "occupied": used.get(c, 0)} for c in sorted(totals)]


def by_hour(days=DAYS):
    """Loads and unloads per local hour of day over the chart window."""
    start = _day_start(timezone.localdate() - timedelta(days=days - 1))
    hours = [{"hour": h, "loads": 0, "unloads": 0} for h in range(24)]
    for op_type, created in OperationLog.objects.filter(created_at__gte=start).values_list("op_type", "created_at"):
        hours[timezone.localtime(created).hour]["loads" if op_type == OperationLog.LOAD else "unloads"] += 1
    return hours


def average_dwell_hours(days=DAYS):
    """Mean time in stock of pallets unloaded during the window; None when none were unloaded."""
    since = _day_start(timezone.localdate() - timedelta(days=days - 1))
    spans = [
        (out - inn).total_seconds()
        for inn, out in Pallet.objects.filter(status=Pallet.STATUS_UNLOADED, unloaded_at__gte=since)
        .values_list("loaded_at", "unloaded_at")
    ]
    return round(sum(spans) / len(spans) / 3600, 1) if spans else None


def aging(min_days):
    cutoff = timezone.now() - timedelta(days=min_days)
    now = timezone.now()
    qs = (
        Pallet.objects.filter(status=Pallet.STATUS_ACTIVE, loaded_at__lte=cutoff)
        .select_related("location", "loaded_by")
        .order_by("loaded_at", "id")
    )
    return [(p, round((now - p.loaded_at).total_seconds() / 86400, 1)) for p in qs]
