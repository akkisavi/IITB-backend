
"""Django email backend using Gmail's HTTPS API."""

import base64

from django.conf import settings
from django.core.mail.backends.base import BaseEmailBackend

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError


class GmailEmailBackend(BaseEmailBackend):

    def send_messages(self, email_messages):
        if not email_messages:
            return 0

        sent = 0

        try:
            credentials = Credentials(
                token=None,
                refresh_token=settings.GMAIL_REFRESH_TOKEN,
                token_uri="https://oauth2.googleapis.com/token",
                client_id=settings.GMAIL_CLIENT_ID,
                client_secret=settings.GMAIL_CLIENT_SECRET,
                scopes=[
                    "https://www.googleapis.com/auth/gmail.send"
                ],
            )

            service = build(
                "gmail",
                "v1",
                credentials=credentials,
                cache_discovery=False,
            )

            for message in email_messages:
                if not message.recipients():
                    continue

                message.from_email = settings.GMAIL_SENDER_EMAIL

                mime_message = message.message()

                if message.bcc:
                    mime_message["Bcc"] = ", ".join(message.bcc)

                raw_message = base64.urlsafe_b64encode(
                    mime_message.as_bytes()
                ).decode("utf-8")

                try:
                    service.users().messages().send(
                        userId="me",
                        body={"raw": raw_message},
                    ).execute()

                    sent += 1

                except HttpError as exc:
                    if not self.fail_silently:
                        raise OSError(
                            f"Gmail API returned an error: {exc}"
                        ) from exc

        except Exception:
            if not self.fail_silently:
                raise

        return sent
