from rest_framework.permissions import BasePermission

from accounts.models import get_role


class _RolePermission(BasePermission):
    roles = ()

    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and get_role(user) in self.roles)


class IsOperator(_RolePermission):
    """Load/unload: admin or technician."""

    roles = ("admin", "technician")


class IsAuditorOrAdmin(_RolePermission):
    roles = ("admin", "auditor")


class IsAdminRole(_RolePermission):
    roles = ("admin",)
