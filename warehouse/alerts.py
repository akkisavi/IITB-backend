"""Low-battery email alert for the shuttle.

Sent once when battery_percent drops from >= threshold to below it. Rising back above the
threshold re-arms the alert. Test alerts are sent directly by the API view so it can report
provider failures. Automatic mail failures are logged without interrupting a save.
"""
import logging
import threading

from django.conf import settings
from django.core.mail import send_mail
from django.db import transaction
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver

from .emails import low_battery_email
from .models import Shuttle

log = logging.getLogger(__name__)


@receiver(pre_save, sender=Shuttle)
def _remember_old_battery(sender, instance, **kwargs):
    old = None
    if instance.pk:
        old = Shuttle.objects.filter(pk=instance.pk).values_list("battery_percent", flat=True).first()
    instance._old_battery = old


@receiver(post_save, sender=Shuttle)
def _alert_on_low_battery(sender, instance, created, **kwargs):
    threshold = Shuttle.LOW_BATTERY_THRESHOLD
    old = getattr(instance, "_old_battery", None)
    now_low = instance.battery_percent < threshold
    was_low = old is not None and old < threshold
    identifier, battery = instance.identifier, instance.battery_percent
    if now_low and not was_low and not getattr(instance, "_test_alert_to", None):
        transaction.on_commit(lambda: _send_in_background(identifier, battery))


def _send_in_background(identifier, battery, recipients=None, test=False):
    # Automatic email can take seconds; don't make a read or save wait for it.
    args = (identifier, battery, recipients, test)
    if settings.EMAIL_BACKEND in ("django.core.mail.backends.smtp.EmailBackend", "warehouse.mail_backend.ResendEmailBackend"):
        threading.Thread(target=send_low_battery_alert, args=args, daemon=True).start()
    else:
        send_low_battery_alert(*args)


def send_low_battery_alert(identifier, battery, recipients=None, test=False):
    recipients = recipients or settings.LOW_BATTERY_ALERT_TO
    if not recipients:
        log.warning("Low battery on %s (%s%%) but LOW_BATTERY_ALERT_TO is empty", identifier, battery)
        return False
    try:
        sent = send_mail(
            subject=f"{'[Test] ' if test else ''}Low battery: shuttle {identifier} at {battery}%",
            message=(
                f"Shuttle {identifier} battery is at {battery}%, below the "
                f"{Shuttle.LOW_BATTERY_THRESHOLD}% threshold. Send it to charge."
            ),
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=recipients,
            html_message=low_battery_email(identifier, battery, Shuttle.LOW_BATTERY_THRESHOLD, test),
        )
        if sent:
            log.info("Low-battery alert accepted for %s to %s", identifier, recipients)
        return bool(sent)
    except Exception:
        log.exception("Failed to send low-battery alert for %s", identifier)
        return False
