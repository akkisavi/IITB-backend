import csv

from django.db.models import Count, Q, Sum
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from .exceptions import ApiError, field_errors
from .idempotency import idempotent
from . import analytics
from .models import CAPACITY, Location, OperationLog, Pallet, Shuttle, ShuttleMaintenance
from .pagination import StandardPagination
from .permissions import IsAuditorOrAdmin, IsOperator
from .serializers import (
    iso, location_data, maintenance_data, operation_data, pallet_data, shuttle_data, validate_bulk_unload,
    validate_maintenance, validate_shuttle_update,
)
from .simulation import simulate
from .services import bulk_unload, latest_operation, load_pallet, undo_operation, unload_pallet


class HealthView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request):
        return Response({"status": "ok"})


class LocationListView(APIView):
    def get(self, request):
        active = {
            p.location_id: p
            for p in Pallet.objects.filter(status=Pallet.STATUS_ACTIVE).select_related("loaded_by", "location")
        }
        return Response([location_data(loc, active.get(loc.id)) for loc in Location.objects.all()])


class LocationDetailView(APIView):
    def get(self, request, code):
        loc = get_object_or_404(Location, code=code.strip().upper())
        pallet = (
            Pallet.objects.filter(location=loc, status=Pallet.STATUS_ACTIVE)
            .select_related("loaded_by", "location")
            .first()
        )
        return Response(location_data(loc, pallet))


class LoadView(APIView):
    permission_classes = [IsOperator]

    def post(self, request):
        def run():
            op, pallet, loc = load_pallet(request.data, request.user)
            body = {
                "operation": operation_data(op, include_pallet_id=False),
                "pallet": pallet_data(pallet),
                "location": location_data(loc, pallet),
            }
            return body, status.HTTP_201_CREATED

        return idempotent(request, "load", run)


class UnloadView(APIView):
    permission_classes = [IsOperator]

    def post(self, request):
        def run():
            op, pallet, loc = unload_pallet(request.data, request.user)
            body = {
                "operation": operation_data(op, include_pallet_id=False),
                "pallet": pallet_data(pallet),
                "location": location_data(loc, None),
            }
            return body, status.HTTP_200_OK

        return idempotent(request, "unload", run)


PALLET_ORDERINGS = {
    "loaded_at", "-loaded_at", "pallet_number", "-pallet_number",
    "item_name", "-item_name", "quantity", "-quantity",
}


def paginate(request, view, queryset, to_data):
    paginator = StandardPagination()
    page = paginator.paginate_queryset(queryset, request, view=view)
    return paginator.get_paginated_response([to_data(obj) for obj in page])


class PalletListView(APIView):
    def get(self, request):
        qp = request.query_params
        qs = Pallet.objects.select_related("location", "loaded_by", "unloaded_by")
        status_f = qp.get("status", "").strip() or "active"
        if status_f not in ("active", "unloaded", "all"):
            field_errors({"status": ["Status must be active, unloaded or all."]})
        if status_f != "all":
            qs = qs.filter(status=status_f)
        search = qp.get("search", "").strip()
        if search:
            qs = qs.filter(
                Q(pallet_number__icontains=search) | Q(barcode__icontains=search) | Q(item_name__icontains=search)
            )
        location = qp.get("location", "").strip().upper()
        if location:
            qs = qs.filter(location__code=location)
        ordering = qp.get("ordering", "").strip() or "-loaded_at"
        if ordering not in PALLET_ORDERINGS:
            allowed = ", ".join(sorted(PALLET_ORDERINGS))
            field_errors({"ordering": [f"Ordering must be one of: {allowed}."]})
        return paginate(request, self, qs.order_by(ordering, "-id"), pallet_data)


class PalletDetailView(APIView):
    def get(self, request, pk):
        pallet = get_object_or_404(Pallet.objects.select_related("location", "loaded_by", "unloaded_by"), pk=pk)
        return Response(pallet_data(pallet))


class PalletHistoryView(APIView):
    def get(self, request, pk):
        pallet = get_object_or_404(Pallet.objects.select_related("location", "loaded_by", "unloaded_by"), pk=pk)
        ops = OperationLog.objects.filter(pallet=pallet).order_by("created_at", "id")
        last = latest_operation(pallet.pk)
        undoable = last.id if last and last.reverses_id is None else None
        return Response(
            {"pallet": pallet_data(pallet), "operations": [operation_data(o) for o in ops],
             "undoable_operation_id": undoable}
        )


class OperationListView(APIView):
    def get(self, request):
        qp = request.query_params
        qs = OperationLog.objects.all()
        op_type = qp.get("op_type", "").strip().upper()
        if op_type:
            if op_type not in (OperationLog.LOAD, OperationLog.UNLOAD):
                field_errors({"op_type": ["op_type must be LOAD or UNLOAD."]})
            qs = qs.filter(op_type=op_type)
        location = qp.get("location", "").strip().upper()
        if location:
            qs = qs.filter(location_code=location)
        search = qp.get("search", "").strip()
        if search:
            qs = qs.filter(
                Q(pallet_number__icontains=search) | Q(item_name__icontains=search) | Q(username__icontains=search)
            )
        since_id = qp.get("since_id", "").strip()
        if since_id:
            if not since_id.isdigit():
                field_errors({"since_id": ["since_id must be a non-negative integer."]})
            return paginate(request, self, qs.filter(id__gt=int(since_id)).order_by("id"), operation_data)
        return paginate(request, self, qs.order_by("-created_at", "-id"), operation_data)


class UndoView(APIView):
    permission_classes = [IsOperator]

    def post(self, request, pk):
        op, pallet, created = undo_operation(pk, request.user)
        pallet = Pallet.objects.select_related("location", "loaded_by", "unloaded_by").get(pk=pallet.pk)
        return Response(
            {"operation": operation_data(op), "pallet": pallet_data(pallet), "replayed": not created},
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


class BulkUnloadView(APIView):
    permission_classes = [IsOperator]

    def post(self, request):
        results = bulk_unload(validate_bulk_unload(request.data), request.user)
        ok = sum(r["ok"] for r in results)
        return Response({"results": results, "succeeded": ok, "failed": len(results) - ok})


def _shuttle():
    shuttle = Shuttle.objects.order_by("identifier").first()
    if shuttle is None:
        raise ApiError(404, "not_found", "No shuttle is configured.")
    return shuttle


class ShuttleView(APIView):
    def get_permissions(self):
        return [IsOperator()] if self.request.method == "PATCH" else super().get_permissions()

    def get(self, request):
        return Response(shuttle_data(simulate(_shuttle())))

    def patch(self, request):
        changes = validate_shuttle_update(request.data)
        test_to = changes.pop("test_alert_email", None)
        shuttle = _shuttle()
        for field, value in changes.items():
            setattr(shuttle, field, value)
        shuttle._test_alert_to = test_to
        shuttle.save(update_fields=[*changes, "updated_at"])
        data = shuttle_data(shuttle)
        if test_to:
            data["test_alert_sent_to"] = test_to
        return Response(data)


class ShuttleMaintenanceView(APIView):
    def get_permissions(self):
        return [IsOperator()] if self.request.method == "POST" else super().get_permissions()

    def get(self, request):
        entries = ShuttleMaintenance.objects.filter(shuttle=_shuttle())[:50]
        return Response([maintenance_data(m) for m in entries])

    def post(self, request):
        note = validate_maintenance(request.data)
        entry = ShuttleMaintenance.objects.create(
            shuttle=_shuttle(), note=note, user=request.user, username=request.user.username
        )
        return Response(maintenance_data(entry), status=status.HTTP_201_CREATED)


def _parse_days(request, default=7):
    raw = request.query_params.get("days", "").strip() or str(default)
    if not raw.isdigit() or not 0 <= int(raw) <= 3650:
        field_errors({"days": ["days must be a whole number between 0 and 3650."]})
    return int(raw)


class DashboardView(APIView):
    permission_classes = [IsAuditorOrAdmin]

    def get(self, request):
        active = Pallet.objects.filter(status=Pallet.STATUS_ACTIVE)
        occupied = min(active.count(), CAPACITY)
        history = analytics.daily_series()
        top_items = (
            active.values("item_name")
            .annotate(pallets=Count("id"), quantity=Sum("quantity"))
            .order_by("-quantity", "item_name")[:5]
        )
        oldest = active.select_related("location", "loaded_by").order_by("loaded_at", "id")[:5]
        recent = OperationLog.objects.order_by("-created_at", "-id")[:10]
        return Response(
            {
                "capacity": CAPACITY,
                "occupied": occupied,
                "free": CAPACITY - occupied,
                "occupancy_pct": round(occupied / CAPACITY * 100, 1),
                "history": history,
                "by_column": analytics.by_column(),
                "by_hour": analytics.by_hour(),
                "avg_dwell_hours": analytics.average_dwell_hours(),
                "recent_operations": [operation_data(op) for op in recent],
                "top_items": [
                    {"item_name": t["item_name"], "pallets": t["pallets"], "quantity": t["quantity"]}
                    for t in top_items
                ],
                "oldest_pallets": [pallet_data(p) for p in oldest],
                "loads_today": history[-1]["loads"],
                "unloads_today": history[-1]["unloads"],
                "timezone": str(timezone.get_current_timezone()),
                "generated_at": iso(timezone.now()),
            }
        )


class AgingReportView(APIView):
    def get(self, request):
        days = _parse_days(request)
        rows = analytics.aging(days)
        return Response({"days": days, "results": [{**pallet_data(p), "age_days": age} for p, age in rows]})


def _csv(filename, header, rows):
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    writer = csv.writer(response)
    writer.writerow(header)
    writer.writerows(rows)
    return response


class ReportView(APIView):
    """CSV downloads. Only a JSON renderer is configured, so clients send Accept: */* here."""

    permission_classes = [IsAuditorOrAdmin]

    def get(self, request, name):
        stamp = timezone.localdate().isoformat()
        if name == "stock":
            now = timezone.now()
            qs = Pallet.objects.filter(status=Pallet.STATUS_ACTIVE).select_related("location", "loaded_by")
            return _csv(
                f"stock-{stamp}.csv",
                ["location", "pallet_number", "barcode", "item_name", "quantity", "loaded_at", "loaded_by", "age_days"],
                [
                    [p.location.code, p.pallet_number, p.barcode, p.item_name, p.quantity, iso(p.loaded_at),
                     p.loaded_by.username if p.loaded_by_id else "",
                     round((now - p.loaded_at).total_seconds() / 86400, 1)]
                    for p in qs.order_by("location__code")
                ],
            )
        if name == "history":
            qs = OperationLog.objects.order_by("created_at", "id")
            pallet = request.query_params.get("pallet", "").strip()
            if pallet.isdigit():
                qs = qs.filter(pallet_id=int(pallet))
            return _csv(
                f"pallet-history-{stamp}.csv",
                ["id", "created_at", "op_type", "location", "pallet_number", "item_name", "quantity", "user",
                 "reverses"],
                [
                    [o.id, iso(o.created_at), o.op_type, o.location_code, o.pallet_number, o.item_name, o.quantity,
                     o.username, o.reverses_id or ""]
                    for o in qs.iterator()
                ],
            )
        if name == "occupancy":
            return _csv(
                f"occupancy-7d-{stamp}.csv",
                ["date", "occupied", "free", "capacity", "loads", "unloads", "basis"],
                [[p["date"], p["occupied"], p["free"], CAPACITY, p["loads"], p["unloads"], p["source"]]
                 for p in analytics.daily_series()],
            )
        if name == "aging":
            days = _parse_days(request)
            return _csv(
                f"stock-aging-{days}d-{stamp}.csv",
                ["location", "pallet_number", "item_name", "quantity", "loaded_at", "age_days"],
                [[p.location.code, p.pallet_number, p.item_name, p.quantity, iso(p.loaded_at), age]
                 for p, age in analytics.aging(days)],
            )
        raise ApiError(404, "not_found", "Unknown report.")
