"""Django email backend using Resend's HTTPS API."""
import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from django.conf import settings
from django.core.mail.backends.base import BaseEmailBackend


class ResendEmailBackend(BaseEmailBackend):
    def send_messages(self, email_messages):
        if not email_messages:
            return 0
        sent = 0
        for message in email_messages:
            html = next((body for body, mime_type in message.alternatives if mime_type == "text/html"), None)
            payload = {
                "from": message.from_email,
                "to": message.to,
                "subject": message.subject,
                "text": message.body,
            }
            if html:
                payload["html"] = html
            request = Request(
                "https://api.resend.com/emails",
                data=json.dumps(payload).encode("utf-8"),
                headers={
                    "Authorization": f"Bearer {settings.RESEND_API_KEY}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            try:
                with urlopen(request, timeout=10) as response:
                    if response.status not in (200, 201):
                        raise OSError(f"Resend returned HTTP {response.status}")
            except HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")[:1000]
                if self.fail_silently:
                    return sent
                raise OSError(f"Resend returned HTTP {exc.code}: {detail}") from exc
            except (OSError, URLError):
                if self.fail_silently:
                    return sent
                raise
            sent += 1
        return sent
