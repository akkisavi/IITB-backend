import re

from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth.models import update_last_login
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.authtoken.models import Token
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from warehouse.exceptions import ApiError, field_errors
from warehouse.pagination import StandardPagination
from warehouse.permissions import IsAdminRole
from warehouse.serializers import iso

from .models import Role, UserProfile, active_admin_ids, get_profile, get_role

User = get_user_model()

USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{3,150}$")
MSG_USERNAME = "Username must be 3-150 characters: letters, digits, _ . -"
MSG_USERNAME_TAKEN = "A user with that username already exists."
MSG_PASSWORD = "Password must be 12-128 characters with no spaces."
MSG_ROLE = "Role must be admin, auditor or technician."
MSG_SELF = "You cannot deactivate or demote your own account."
MSG_LAST_ADMIN = "This change would leave the system without an active admin."
ROLES = {r.value for r in Role}


def me_data(user):
    return {"id": user.id, "username": user.username, "role": get_role(user)}


def user_data(user):
    return {
        "id": user.id,
        "username": user.username,
        "role": get_role(user),
        "is_active": user.is_active,
        "is_superuser": user.is_superuser,
        "date_joined": iso(user.date_joined),
        "last_login": iso(user.last_login),
    }


def password_errors(password, user=None):
    """Return a list of messages (empty when the password is acceptable)."""
    if not isinstance(password, str) or not (12 <= len(password) <= 128) or re.search(r"\s", password):
        return [MSG_PASSWORD]
    try:
        validate_password(password, user=user)
    except DjangoValidationError as exc:
        return list(exc.messages)
    return []


class LoginView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        data = request.data if isinstance(request.data, dict) else {}
        username = data.get("username")
        password = data.get("password")
        username = username.strip() if isinstance(username, str) else ""
        errors = {}
        if not username:
            errors["username"] = ["Username is required."]
        if not isinstance(password, str) or not password:
            errors["password"] = ["Password is required."]
        if errors:
            field_errors(errors)
        user = authenticate(request, username=username, password=password)
        if user is None or not user.is_active:
            raise ApiError(400, "invalid_credentials", "Invalid username or password.")
        token, _ = Token.objects.get_or_create(user=user)
        update_last_login(None, user)
        return Response({"token": token.key, "user": me_data(user)})


class LogoutView(APIView):
    def post(self, request):
        Token.objects.filter(user=request.user).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class MeView(APIView):
    def get(self, request):
        return Response(me_data(request.user))


class ProfileView(APIView):
    """The signed-in user's own account details and recent stock movements."""

    def get(self, request):
        from warehouse.models import OperationLog
        from warehouse.serializers import operation_data

        ops = OperationLog.objects.filter(user=request.user)
        counts = {row["op_type"]: row["n"] for row in ops.values("op_type").annotate(n=Count("id"))}
        recent = ops.select_related("pallet").order_by("-created_at", "-id")[:10]
        return Response({
            **user_data(request.user),
            "activity": {
                "loads": counts.get(OperationLog.LOAD, 0),
                "unloads": counts.get(OperationLog.UNLOAD, 0),
                "recent": [operation_data(o) for o in recent],
            },
        })


class ChangePasswordView(APIView):
    """Change your own password. Other sessions are signed out; this one gets a fresh token."""

    def post(self, request):
        data = request.data if isinstance(request.data, dict) else {}
        current = data.get("current_password")
        new = data.get("new_password")
        if not isinstance(current, str) or not request.user.check_password(current):
            field_errors({"current_password": ["Current password is incorrect."]})
        errs = password_errors(new, request.user)
        if not errs and new == current:
            errs = ["New password must be different from the current one."]
        if errs:
            field_errors({"new_password": errs})
        with transaction.atomic():
            request.user.set_password(new)
            request.user.save(update_fields=["password"])
            Token.objects.filter(user=request.user).delete()
            token = Token.objects.create(user=request.user)
        return Response({"token": token.key})


def _would_remove_admin(target, new_role=None, new_active=None):
    """True when the change removes target from the set of active admins."""
    was_admin = target.is_active and get_role(target) == Role.ADMIN
    if not was_admin:
        return False
    still_active = target.is_active if new_active is None else new_active
    still_admin = target.is_superuser or (new_role or get_profile(target).role) == Role.ADMIN
    return not (still_active and still_admin)


def _guard_admin_change(request, target, new_role=None, new_active=None):
    is_self = target.pk == request.user.pk
    deactivating = new_active is False
    demoting = new_role is not None and new_role != Role.ADMIN and get_role(target) == Role.ADMIN
    if is_self and (deactivating or demoting):
        raise ApiError(400, "self_action_forbidden", MSG_SELF)
    if _would_remove_admin(target, new_role, new_active):
        # Lock admin rows so two concurrent demotions cannot both pass.
        list(User.objects.select_for_update().filter(pk__in=active_admin_ids()))
        if not active_admin_ids(exclude_pk=target.pk):
            raise ApiError(400, "last_admin", MSG_LAST_ADMIN)


class UserListCreateView(APIView):
    permission_classes = [IsAdminRole]

    def get(self, request):
        qp = request.query_params
        qs = User.objects.select_related("profile").order_by("username")
        search = qp.get("search", "").strip()
        if search:
            qs = qs.filter(username__icontains=search)
        role = qp.get("role", "").strip().lower()
        if role:
            if role not in ROLES:
                field_errors({"role": [MSG_ROLE]})
            if role == Role.ADMIN:
                qs = qs.filter(Q(is_superuser=True) | Q(profile__role=Role.ADMIN))
            else:
                qs = qs.filter(is_superuser=False, profile__role=role)
        is_active = qp.get("is_active", "").strip().lower()
        if is_active:
            if is_active not in ("true", "false"):
                field_errors({"is_active": ["is_active must be true or false."]})
            qs = qs.filter(is_active=(is_active == "true"))
        paginator = StandardPagination()
        page = paginator.paginate_queryset(qs, request, view=self)
        return paginator.get_paginated_response([user_data(u) for u in page])

    def post(self, request):
        data = request.data if isinstance(request.data, dict) else {}
        errors = {}
        username = data.get("username")
        username = username.strip() if isinstance(username, str) else ""
        if not USERNAME_RE.match(username):
            errors["username"] = [MSG_USERNAME]
        elif User.objects.filter(username__iexact=username).exists():
            errors["username"] = [MSG_USERNAME_TAKEN]
        role = data.get("role")
        if role not in ROLES:
            errors["role"] = [MSG_ROLE]
        pw_errs = password_errors(data.get("password"), User(username=username))
        if pw_errs:
            errors["password"] = pw_errs
        if errors:
            field_errors(errors)
        with transaction.atomic():
            user = User(username=username, is_active=True)
            user.set_password(data["password"])
            user.save()
            UserProfile.objects.update_or_create(user=user, defaults={"role": role})
        user.refresh_from_db()
        return Response(user_data(user), status=status.HTTP_201_CREATED)


class UserDetailView(APIView):
    permission_classes = [IsAdminRole]
    EDITABLE = {"role", "is_active"}

    def get(self, request, pk):
        return Response(user_data(get_object_or_404(User, pk=pk)))

    def patch(self, request, pk):
        data = request.data if isinstance(request.data, dict) else None
        if data is None:
            field_errors({"non_field_errors": ["Expected a JSON object."]})
        errors = {key: ["Field not editable."] for key in data if key not in self.EDITABLE}
        new_role = data.get("role") if "role" in data else None
        new_active = data.get("is_active") if "is_active" in data else None
        if "role" in data and new_role not in ROLES:
            errors["role"] = [MSG_ROLE]
        if "is_active" in data and not isinstance(new_active, bool):
            errors["is_active"] = ["is_active must be true or false."]
        if errors:
            field_errors(errors)
        with transaction.atomic():
            target = get_object_or_404(User.objects.select_for_update(), pk=pk)
            _guard_admin_change(request, target, new_role=new_role, new_active=new_active)
            if new_role is not None:
                profile = get_profile(target)
                profile.role = new_role
                profile.save(update_fields=["role"])
            if new_active is not None and new_active != target.is_active:
                target.is_active = new_active
                target.save(update_fields=["is_active"])
                if not new_active:
                    Token.objects.filter(user=target).delete()
        target = User.objects.get(pk=target.pk)
        return Response(user_data(target))

    def delete(self, request, pk):
        with transaction.atomic():
            target = get_object_or_404(User.objects.select_for_update(), pk=pk)
            _guard_admin_change(request, target, new_active=False)
            target.is_active = False
            target.save(update_fields=["is_active"])
            Token.objects.filter(user=target).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class UserSetPasswordView(APIView):
    permission_classes = [IsAdminRole]

    def post(self, request, pk):
        target = get_object_or_404(User, pk=pk)
        data = request.data if isinstance(request.data, dict) else {}
        errs = password_errors(data.get("password"), target)
        if errs:
            field_errors({"password": errs})
        with transaction.atomic():
            target.set_password(data["password"])
            target.save(update_fields=["password"])
            Token.objects.filter(user=target).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
