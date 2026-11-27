from datetime import timedelta

from django.utils import timezone
from rest_framework.authentication import TokenAuthentication
from rest_framework.exceptions import AuthenticationFailed

REMEMBER_TTL = timedelta(days=7)
SHORT_TTL = timedelta(hours=12)


def start_session(token, remember):
    from .models import TokenSession

    expires = timezone.now() + (REMEMBER_TTL if remember else SHORT_TTL)
    session, created = TokenSession.objects.get_or_create(
        token=token, defaults={"remember": remember, "expires_at": expires}
    )
    if not created and expires > session.expires_at:
        session.expires_at = expires
        session.remember = session.remember or remember
        session.save(update_fields=["expires_at", "remember"])
    return session


class ExpiringTokenAuthentication(TokenAuthentication):
    def authenticate_credentials(self, key):
        user, token = super().authenticate_credentials(key)
        session = getattr(token, "session", None)
        expires = session.expires_at if session else token.created + REMEMBER_TTL
        if expires <= timezone.now():
            token.delete()
            raise AuthenticationFailed("Your session has expired. Please sign in again.")
        return user, token
