from django.conf import settings
from django.db import models


class Role(models.TextChoices):
    ADMIN = "admin", "Admin"
    AUDITOR = "auditor", "Auditor"
    TECHNICIAN = "technician", "Technician"


class UserProfile(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="profile")
    role = models.CharField(max_length=16, choices=Role.choices, default=Role.TECHNICIAN)

    def __str__(self):
        return f"{self.user.username} ({self.role})"


def get_profile(user):
    try:
        return user.profile
    except UserProfile.DoesNotExist:
        profile, _ = UserProfile.objects.get_or_create(user=user)
        return profile


def get_role(user):
    """Superusers are always admin; otherwise the profile role. Never inferred from is_staff."""
    if user is None or not user.is_authenticated:
        return None
    if user.is_superuser:
        return Role.ADMIN.value
    return get_profile(user).role


def active_admin_ids(exclude_pk=None):
    """Ids of active users whose effective role is admin."""
    from django.contrib.auth import get_user_model
    from django.db.models import Q

    User = get_user_model()
    qs = User.objects.filter(is_active=True).filter(Q(is_superuser=True) | Q(profile__role=Role.ADMIN))
    if exclude_pk is not None:
        qs = qs.exclude(pk=exclude_pk)
    return set(qs.values_list("pk", flat=True))
