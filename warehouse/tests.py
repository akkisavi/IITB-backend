import os
from datetime import timedelta
from io import StringIO
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.utils import timezone
from django.test import override_settings
from rest_framework.authtoken.models import Token
from rest_framework.test import APITestCase

from accounts.models import UserProfile
from warehouse.models import DailyOccupancySnapshot, Location, OperationLog, Pallet, Shuttle

User = get_user_model()
PW = "Unit-Test-Pass-123"


def make_user(username, role, **extra):
    user = User.objects.create_user(username=username, password=PW, **extra)
    UserProfile.objects.update_or_create(user=user, defaults={"role": role})
    return user


class BaseAPITest(APITestCase):
    def setUp(self):
        self.admin = make_user("admin1", "admin")
        self.auditor = make_user("auditor1", "auditor")
        self.tech = make_user("tech1", "technician")

    def as_user(self, user):
        token, _ = Token.objects.get_or_create(user=user)
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")

    def anon(self):
        self.client.credentials()

    def load(self, **overrides):
        body = {"pallet_number": "plt-0042", "barcode": "40012345678901", "item_name": "  Copper   wire ",
                "quantity": 120, "location": "c3"}
        body.update(overrides)
        return self.client.post("/api/operations/load/", body, format="json")


class HealthAndAuthTests(BaseAPITest):
    def test_health_is_public(self):
        res = self.client.get("/health/")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json(), {"status": "ok"})

    def test_login_me_logout(self):
        res = self.client.post("/api/auth/login/", {"username": " tech1 ", "password": PW}, format="json")
        self.assertEqual(res.status_code, 200)
        token = res.json()["token"]
        self.assertEqual(len(token), 40)
        self.assertEqual(res.json()["user"]["role"], "technician")
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {token}")
        self.assertEqual(self.client.get("/api/auth/me/").json()["username"], "tech1")
        self.assertEqual(self.client.post("/api/auth/logout/").status_code, 204)
        res = self.client.get("/api/auth/me/")
        self.assertEqual(res.status_code, 401)
        self.assertEqual(res.json()["code"], "authentication_failed")

    def test_bad_login_and_inactive_login(self):
        res = self.client.post("/api/auth/login/", {"username": "tech1", "password": "wrong-password"}, format="json")
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()["code"], "invalid_credentials")
        self.tech.is_active = False
        self.tech.save()
        res = self.client.post("/api/auth/login/", {"username": "tech1", "password": PW}, format="json")
        self.assertEqual(res.json()["code"], "invalid_credentials")

    def test_unauthenticated_shape(self):
        res = self.client.get("/api/locations/")
        self.assertEqual(res.status_code, 401)
        self.assertEqual(res.json()["code"], "not_authenticated")
        self.assertIn("detail", res.json())


class PermissionMatrixTests(BaseAPITest):
    def test_matrix(self):
        reads = ["/api/auth/me/", "/api/locations/", "/api/locations/b2/", "/api/pallets/", "/api/operations/",
                 "/api/shuttle/"]
        for path in reads:
            self.anon()
            self.assertEqual(self.client.get(path).status_code, 401, path)
            for user in (self.admin, self.auditor, self.tech):
                self.as_user(user)
                self.assertEqual(self.client.get(path).status_code, 200, (path, user.username))

        expected = {
            "/api/dashboard/": {"admin1": 200, "auditor1": 200, "tech1": 403},
            "/api/users/": {"admin1": 200, "auditor1": 403, "tech1": 403},
        }
        for path, by_user in expected.items():
            self.anon()
            self.assertEqual(self.client.get(path).status_code, 401)
            for user in (self.admin, self.auditor, self.tech):
                self.as_user(user)
                res = self.client.get(path)
                self.assertEqual(res.status_code, by_user[user.username], (path, user.username))
                if res.status_code == 403:
                    self.assertEqual(res.json()["code"], "permission_denied")

    def test_auditor_cannot_load_or_unload(self):
        self.as_user(self.auditor)
        self.assertEqual(self.load().status_code, 403)
        res = self.client.post("/api/operations/unload/", {"location": "C3"}, format="json")
        self.assertEqual(res.status_code, 403)
        self.anon()
        self.assertEqual(self.load().status_code, 401)
        self.assertFalse(Pallet.objects.exists())

    def test_admin_can_load(self):
        self.as_user(self.admin)
        self.assertEqual(self.load().status_code, 201)


class LoadUnloadTests(BaseAPITest):
    def test_locations_grid(self):
        self.as_user(self.tech)
        data = self.client.get("/api/locations/").json()
        self.assertEqual(len(data), 25)
        self.assertEqual([d["code"] for d in data[:6]], ["A1", "B1", "C1", "D1", "E1", "A2"])
        self.assertEqual(data[0]["state"], "transfer")
        self.assertEqual(sum(1 for d in data if d["kind"] == "storage"), 24)

    def test_load_then_unload(self):
        self.as_user(self.tech)
        res = self.load()
        self.assertEqual(res.status_code, 201, res.json())
        body = res.json()
        self.assertEqual(body["operation"]["op_type"], "LOAD")
        self.assertEqual(body["operation"]["location_code"], "C3")
        self.assertEqual(body["pallet"]["pallet_number"], "PLT-0042")
        self.assertEqual(body["pallet"]["item_name"], "Copper wire")
        self.assertEqual(body["pallet"]["loaded_by"], "tech1")
        self.assertTrue(body["pallet"]["loaded_at"].endswith("Z"))
        self.assertEqual(body["location"]["state"], "occupied")
        detail = self.client.get("/api/locations/C3/").json()
        self.assertEqual(detail["pallet"]["barcode"], "40012345678901")
        snap = DailyOccupancySnapshot.objects.get(date=timezone.localdate())
        self.assertEqual((snap.occupied, snap.free, snap.source), (1, 23, "live"))

        res = self.client.post("/api/operations/unload/", {"location": "c3"}, format="json")
        self.assertEqual(res.status_code, 200, res.json())
        body = res.json()
        self.assertEqual(body["pallet"]["status"], "unloaded")
        self.assertIsNone(body["pallet"]["location"])
        self.assertEqual(body["pallet"]["unloaded_by"], "tech1")
        self.assertEqual(body["location"]["state"], "free")
        self.assertEqual(OperationLog.objects.count(), 2)
        self.assertEqual(DailyOccupancySnapshot.objects.get(date=timezone.localdate()).occupied, 0)
        self.assertEqual(self.client.get("/api/pallets/").json()["count"], 0)
        self.assertEqual(self.client.get("/api/pallets/?status=all").json()["count"], 1)

        self.assertEqual(self.load(location="D4").status_code, 201)

    def test_validation_errors(self):
        self.as_user(self.tech)
        res = self.client.post("/api/operations/load/", {}, format="json")
        self.assertEqual(res.status_code, 400)
        body = res.json()
        self.assertEqual(body["code"], "validation_error")
        self.assertEqual(set(body["errors"]), {"pallet_number", "barcode", "item_name", "quantity", "location"})
        self.assertEqual(body["errors"]["item_name"], ["Item name is required."])

        cases = [
            ({"pallet_number": "a b"}, "pallet_number", "Pallet number must be 3-32 characters: letters, digits or hyphen."),
            ({"barcode": "1234567"}, "barcode", "Barcode must be 8-14 digits."),
            ({"barcode": "12345678a"}, "barcode", "Barcode must be 8-14 digits."),
            ({"item_name": "   "}, "item_name", "Item name is required."),
            ({"item_name": "x" * 101}, "item_name", "Item name must be at most 100 characters."),
            ({"location": "Z9"}, "location", "Unknown location."),
            ({"location": "a1"}, "location", "A1 is the transfer point and cannot store pallets."),
        ]
        for qty in (0, -1, 100001, 12.5, "12.5", "abc", True, None):
            cases.append(({"quantity": qty}, "quantity", "Quantity must be a whole number between 1 and 100000."))
        for override, field, message in cases:
            res = self.load(**override)
            self.assertEqual(res.status_code, 400, override)
            self.assertEqual(res.json()["errors"][field], [message], override)
        self.assertFalse(Pallet.objects.exists())
        self.assertEqual(self.load(quantity="100000").status_code, 201)

    def test_unload_validation_and_empty(self):
        self.as_user(self.tech)
        res = self.client.post("/api/operations/unload/", {"location": "A1"}, format="json")
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()["errors"]["location"], ["A1 is the transfer point and holds no stock."])
        res = self.client.post("/api/operations/unload/", {"location": "QQ"}, format="json")
        self.assertEqual(res.json()["errors"]["location"], ["Unknown location."])
        res = self.client.post("/api/operations/unload/", {"location": "C3"}, format="json")
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.json()["code"], "location_empty")
        self.assertEqual(res.json()["detail"], "Location C3 is already empty.")

    def test_conflicts(self):
        self.as_user(self.tech)
        self.assertEqual(self.load().status_code, 201)
        res = self.load(pallet_number="PLT-0099", barcode="40099999999999")
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.json()["code"], "location_occupied")
        self.assertEqual(res.json()["detail"], "Location C3 is already occupied by pallet PLT-0042.")
        res = self.load(location="D4", barcode="40099999999999")
        self.assertEqual(res.json()["code"], "duplicate_pallet_number")
        self.assertEqual(res.json()["detail"], "Pallet PLT-0042 is already in the warehouse at C3.")
        res = self.load(location="D4", pallet_number="PLT-0099")
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.json()["code"], "duplicate_barcode")
        self.assertEqual(Pallet.objects.count(), 1)
        self.assertEqual(OperationLog.objects.count(), 1)

    def test_integrity_error_maps_to_409(self):
        """Simulate a race: pre-checks pass but the DB constraint fires."""
        self.as_user(self.tech)
        self.assertEqual(self.load().status_code, 201)
        real_filter = Pallet.objects.filter

        class Empty:
            def first(self):
                return None

            def select_related(self, *a):
                return self

            def filter(self, *a, **k):
                return self

        with mock.patch("warehouse.services.Pallet.objects.filter", side_effect=lambda *a, **k: Empty()), \
                mock.patch("warehouse.services.Pallet.objects.select_related", side_effect=lambda *a: Empty()):
            res = self.load(location="D4", barcode="40099999999999")
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.json()["code"], "duplicate_pallet_number")
        self.assertEqual(real_filter(status="active").count(), 1)
        self.assertEqual(OperationLog.objects.count(), 1)

    def test_pallet_list_filters(self):
        self.as_user(self.tech)
        self.load()
        self.load(pallet_number="PLT-2", barcode="11111111", item_name="Paint", location="B2")
        self.assertEqual(self.client.get("/api/pallets/?search=paint").json()["count"], 1)
        self.assertEqual(self.client.get("/api/pallets/?location=c3").json()["count"], 1)
        res = self.client.get("/api/pallets/?ordering=pallet_number").json()
        self.assertEqual([p["pallet_number"] for p in res["results"]], ["PLT-0042", "PLT-2"])
        self.assertEqual(self.client.get("/api/pallets/?status=bogus").status_code, 400)
        pid = res["results"][0]["id"]
        self.assertEqual(self.client.get(f"/api/pallets/{pid}/").status_code, 200)
        self.assertEqual(self.client.get("/api/pallets/99999/").json()["code"], "not_found")
        ops = self.client.get("/api/operations/?op_type=LOAD&search=tech1").json()
        self.assertEqual(ops["count"], 2)
        self.assertIn("pallet_id", ops["results"][0])

    def test_shuttle(self):
        self.as_user(self.auditor)
        res = self.client.get("/api/shuttle/").json()
        self.assertEqual((res["identifier"], res["status"], res["battery_percent"]), ("SH-01", "idle", 87))


class ProfileTests(BaseAPITest):
    def test_profile_shows_account_and_own_activity(self):
        self.as_user(self.tech)
        self.assertEqual(self.load().status_code, 201)
        self.as_user(self.admin)
        self.assertEqual(self.load(location="D4", pallet_number="PLT-0100", barcode="40012345678918").status_code, 201)
        self.as_user(self.tech)
        res = self.client.get("/api/auth/profile/")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual((data["username"], data["role"]), ("tech1", "technician"))
        self.assertEqual((data["activity"]["loads"], data["activity"]["unloads"]), (1, 0))
        self.assertEqual(len(data["activity"]["recent"]), 1)

    def test_profile_requires_login(self):
        self.anon()
        self.assertEqual(self.client.get("/api/auth/profile/").status_code, 401)

    def test_change_password(self):
        self.as_user(self.auditor)
        old_token = Token.objects.get(user=self.auditor).key
        bad = self.client.post("/api/auth/change-password/", {"current_password": "wrong", "new_password": "Another-pass-123"}, format="json")
        self.assertEqual(bad.status_code, 400)
        self.assertIn("current_password", bad.json()["errors"])
        short = self.client.post("/api/auth/change-password/", {"current_password": PW, "new_password": "short"}, format="json")
        self.assertIn("new_password", short.json()["errors"])
        same = self.client.post("/api/auth/change-password/", {"current_password": PW, "new_password": PW}, format="json")
        self.assertIn("new_password", same.json()["errors"])
        ok = self.client.post("/api/auth/change-password/", {"current_password": PW, "new_password": "Another-pass-123"}, format="json")
        self.assertEqual(ok.status_code, 200)
        new_token = ok.json()["token"]
        self.assertNotEqual(new_token, old_token)
        self.assertFalse(Token.objects.filter(key=old_token).exists())
        self.auditor.refresh_from_db()
        self.assertTrue(self.auditor.check_password("Another-pass-123"))


class ShuttleBatteryTests(BaseAPITest):
    def test_operator_updates_battery_and_status(self):
        self.as_user(self.tech)
        res = self.client.patch("/api/shuttle/", {"battery_percent": 50, "status": "charging"}, format="json")
        self.assertEqual(res.status_code, 200)
        self.assertEqual((res.json()["battery_percent"], res.json()["status"]), (50, "charging"))

    def test_auditor_cannot_update(self):
        self.as_user(self.auditor)
        self.assertEqual(self.client.patch("/api/shuttle/", {"battery_percent": 5}, format="json").status_code, 403)

    def test_validation(self):
        self.as_user(self.admin)
        for body in ({"battery_percent": 101}, {"battery_percent": -1}, {"battery_percent": "50"},
                     {"battery_percent": True}, {"status": "flying"}, {}):
            res = self.client.patch("/api/shuttle/", body, format="json")
            self.assertEqual(res.status_code, 400, body)

    @override_settings(LOW_BATTERY_ALERT_TO=["ops@example.com"],
                       EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_low_battery_email_sent_once_per_drop(self):
        from django.core import mail
        self.as_user(self.tech)
        with self.captureOnCommitCallbacks(execute=True):
            self.client.patch("/api/shuttle/", {"battery_percent": 15}, format="json")
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("15%", mail.outbox[0].subject)
        self.assertEqual(mail.outbox[0].to, ["ops@example.com"])
        with self.captureOnCommitCallbacks(execute=True):
            self.client.patch("/api/shuttle/", {"battery_percent": 10}, format="json")  # still low: no repeat
        self.assertEqual(len(mail.outbox), 1)
        with self.captureOnCommitCallbacks(execute=True):
            self.client.patch("/api/shuttle/", {"battery_percent": 80}, format="json")  # re-arm
            self.client.patch("/api/shuttle/", {"battery_percent": 19}, format="json")
        self.assertEqual(len(mail.outbox), 2)


class TestAlertEmailTests(BaseAPITest):
    @override_settings(LOW_BATTERY_ALERT_TO=["ops@example.com"],
                       EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_test_email_gets_alert_even_when_already_low(self):
        from django.core import mail
        self.as_user(self.tech)
        with self.captureOnCommitCallbacks(execute=True):
            res = self.client.patch("/api/shuttle/", {"battery_percent": 15, "test_alert_email": "tester@example.com"}, format="json")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["test_alert_sent_to"], "tester@example.com")
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["tester@example.com"])
        self.assertTrue(mail.outbox[0].subject.startswith("[Test] "))
        with self.captureOnCommitCallbacks(execute=True):
            self.client.patch("/api/shuttle/", {"battery_percent": 12, "test_alert_email": "tester@example.com"}, format="json")
        self.assertEqual(len(mail.outbox), 2)

    def test_test_email_validation(self):
        self.as_user(self.tech)
        bad = self.client.patch("/api/shuttle/", {"battery_percent": 10, "test_alert_email": "not-an-email"}, format="json")
        self.assertEqual(bad.status_code, 400)
        self.assertIn("test_alert_email", bad.json()["errors"])
        high = self.client.patch("/api/shuttle/", {"battery_percent": 50, "test_alert_email": "tester@example.com"}, format="json")
        self.assertEqual(high.status_code, 400)
        self.assertIn("test_alert_email", high.json()["errors"])

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.console.EmailBackend")
    def test_test_email_reports_missing_mail_provider(self):
        self.as_user(self.tech)
        res = self.client.patch("/api/shuttle/", {"battery_percent": 15, "test_alert_email": "tester@example.com"}, format="json")
        self.assertEqual(res.status_code, 503)
        self.assertEqual(res.json()["code"], "email_delivery_failed")
        self.assertNotEqual(Shuttle.objects.first().battery_percent, 15)

    @override_settings(EMAIL_BACKEND="warehouse.mail_backend.ResendEmailBackend",
                       RESEND_API_KEY="test-key", DEFAULT_FROM_EMAIL="Alerts <alerts@example.com>")
    def test_resend_acceptance_and_rejection_are_reported(self):
        from unittest.mock import patch
        from urllib.error import HTTPError

        self.as_user(self.tech)

        class Accepted:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        with patch("warehouse.mail_backend.urlopen", return_value=Accepted()) as send:
            res = self.client.patch("/api/shuttle/", {"battery_percent": 15, "test_alert_email": "tester@example.com"}, format="json")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["test_alert_sent_to"], "tester@example.com")
        request = send.call_args.args[0]
        self.assertEqual(request.full_url, "https://api.resend.com/emails")
        self.assertEqual(request.get_header("User-agent"), "WarehouseConsole/1.0")
        self.assertIn(b'"to": ["tester@example.com"]', request.data)

        error = HTTPError("https://api.resend.com/emails", 403, "forbidden", {}, None)
        with patch("warehouse.mail_backend.urlopen", side_effect=error):
            res = self.client.patch("/api/shuttle/", {"battery_percent": 12, "test_alert_email": "tester@example.com"}, format="json")
        self.assertEqual(res.status_code, 503)
        self.assertEqual(res.json()["code"], "email_delivery_failed")
        self.assertEqual(Shuttle.objects.first().battery_percent, 15)


class BatterySimulationTests(BaseAPITest):
    def age_shuttle(self, seconds, **fields):
        from warehouse.models import Shuttle
        Shuttle.objects.update(updated_at=timezone.now() - timedelta(seconds=seconds), **fields)

    def get_shuttle(self):
        self.as_user(self.auditor)
        return self.client.get("/api/shuttle/").json()

    def test_busy_drains_and_idle_drains_slower(self):
        self.age_shuttle(200, status="busy", battery_percent=80)
        self.assertEqual(self.get_shuttle()["battery_percent"], 70)  # 200 s / 20 s
        self.age_shuttle(200, status="idle", battery_percent=80)
        self.assertEqual(self.get_shuttle()["battery_percent"], 78)  # 200 s / 90 s

    def test_low_battery_docks_and_charges_to_idle(self):
        self.age_shuttle(40, status="busy", battery_percent=12)
        res = self.get_shuttle()
        self.assertEqual((res["status"], res["battery_percent"]), ("charging", 10))
        self.age_shuttle(6 * 95, status="charging", battery_percent=10)
        res = self.get_shuttle()
        self.assertEqual((res["status"], res["battery_percent"]), ("idle", 100))

    def test_offline_is_frozen_and_flag_disables(self):
        self.age_shuttle(1000, status="offline", battery_percent=50)
        self.assertEqual(self.get_shuttle()["battery_percent"], 50)
        with override_settings(SHUTTLE_SIMULATION=False):
            self.age_shuttle(1000, status="busy", battery_percent=50)
            res = self.get_shuttle()
            self.assertEqual((res["battery_percent"], res["simulated"]), (50, False))

    @override_settings(LOW_BATTERY_ALERT_TO=["ops@example.com"],
                       EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_simulated_drop_below_threshold_sends_alert(self):
        from django.core import mail
        self.age_shuttle(60, status="busy", battery_percent=21)
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(self.get_shuttle()["battery_percent"], 18)
        self.assertEqual(len(mail.outbox), 1)


class PalletHistoryTests(BaseAPITest):
    def test_unloaded_pallet_keeps_storage_location_and_actors(self):
        self.as_user(self.tech)
        self.assertEqual(self.load().status_code, 201)
        self.as_user(self.admin)
        self.assertEqual(self.client.post("/api/operations/unload/", {"location": "C3"}, format="json").status_code, 200)
        rows = self.client.get("/api/pallets/?status=unloaded").json()["results"]
        self.assertEqual(len(rows), 1)
        p = rows[0]
        self.assertIsNone(p["location"])
        self.assertEqual(p["storage_location"], "C3")
        self.assertEqual((p["loaded_by"], p["unloaded_by"]), ("tech1", self.admin.username))
        self.assertIsNotNone(p["unloaded_at"])


class IdempotencyTests(BaseAPITest):
    KEY = "test-key-0001"

    def load_with_key(self, key=KEY, **overrides):
        body = {"pallet_number": "PLT-7001", "barcode": "40070000000001", "item_name": "Rope",
                "quantity": 5, "location": "D2"}
        body.update(overrides)
        return self.client.post("/api/operations/load/", body, format="json", HTTP_IDEMPOTENCY_KEY=key)

    def test_retry_with_same_key_replays_without_second_pallet(self):
        self.as_user(self.tech)
        first = self.load_with_key()
        self.assertEqual(first.status_code, 201)
        retry = self.load_with_key()
        self.assertEqual(retry.status_code, 201)  # same result, not a 409 duplicate
        self.assertEqual(retry.headers.get("Idempotent-Replayed"), "true")
        self.assertEqual(retry.json()["pallet"]["id"], first.json()["pallet"]["id"])
        self.assertEqual(Pallet.objects.filter(pallet_number="PLT-7001").count(), 1)
        self.assertEqual(OperationLog.objects.filter(pallet_number="PLT-7001").count(), 1)

    def test_same_key_different_body_is_rejected(self):
        self.as_user(self.tech)
        self.assertEqual(self.load_with_key().status_code, 201)
        res = self.load_with_key(location="E2")
        self.assertEqual(res.status_code, 422)
        self.assertEqual(res.json()["code"], "idempotency_key_reused")

    def test_without_key_a_repeat_is_a_409_duplicate(self):
        self.as_user(self.tech)
        self.assertEqual(self.load_with_key(key="").status_code, 201)
        res = self.load_with_key(key="")
        self.assertEqual(res.status_code, 409)  # location is checked first, so D2 reports occupied
        self.assertEqual(res.json()["code"], "location_occupied")
        res = self.load_with_key(key="", location="E2")  # same pallet number, free slot
        self.assertEqual(res.json()["code"], "duplicate_pallet_number")

    def test_failed_request_is_not_stored_so_key_can_be_retried(self):
        self.as_user(self.tech)
        bad = self.load_with_key(barcode="12")
        self.assertEqual(bad.status_code, 400)
        self.assertEqual(self.load_with_key().status_code, 201)

    def test_unload_retry_replays(self):
        self.as_user(self.tech)
        self.assertEqual(self.load_with_key().status_code, 201)
        hdr = {"HTTP_IDEMPOTENCY_KEY": "unload-key-01"}
        a = self.client.post("/api/operations/unload/", {"location": "D2"}, format="json", **hdr)
        b = self.client.post("/api/operations/unload/", {"location": "D2"}, format="json", **hdr)
        self.assertEqual((a.status_code, b.status_code), (200, 200))
        self.assertEqual(OperationLog.objects.filter(op_type="UNLOAD").count(), 1)

    def test_keys_are_per_user(self):
        self.as_user(self.tech)
        self.assertEqual(self.load_with_key().status_code, 201)
        self.as_user(self.admin)
        res = self.load_with_key(pallet_number="PLT-7002", barcode="40070000000002", location="E2")
        self.assertEqual(res.status_code, 201)  # admin's same key string is a different key

    def test_malformed_key_is_400(self):
        self.as_user(self.tech)
        self.assertEqual(self.load_with_key(key="bad key!").status_code, 400)


class DashboardTests(BaseAPITest):
    def test_history_shape(self):
        today = timezone.localdate()
        DailyOccupancySnapshot.objects.create(date=today - timedelta(days=3), occupied=4, free=20, source="sample")
        DailyOccupancySnapshot.objects.create(date=today, occupied=20, free=4, source="live")  # ignored for today
        self.as_user(self.tech)
        self.load()
        self.as_user(self.auditor)
        data = self.client.get("/api/dashboard/").json()
        self.assertEqual(len(data["history"]), 7)
        self.assertEqual([h["date"] for h in data["history"]],
                         [(today - timedelta(days=k)).isoformat() for k in range(6, -1, -1)])
        self.assertEqual(data["history"][-1], {"date": today.isoformat(), "occupied": 1, "free": 23,
                                               "source": "live_now"})
        self.assertEqual(data["history"][3]["occupied"], 4)
        self.assertEqual(data["history"][0], {"date": (today - timedelta(days=6)).isoformat(), "occupied": None,
                                              "free": None, "source": "missing"})
        self.assertEqual((data["capacity"], data["occupied"], data["free"], data["occupancy_pct"]), (24, 1, 23, 4.2))
        self.assertEqual(data["loads_today"], 1)
        self.assertEqual(data["top_items"], [{"item_name": "Copper wire", "pallets": 1, "quantity": 120}])
        self.assertEqual(len(data["oldest_pallets"]), 1)
        self.assertEqual(len(data["recent_operations"]), 1)


class UserAdminTests(BaseAPITest):
    def test_create_and_login(self):
        self.as_user(self.admin)
        res = self.client.post("/api/users/", {"username": "jane", "password": "Jane-Strong-Pass1", "role": "auditor"},
                               format="json")
        self.assertEqual(res.status_code, 201, res.json())
        self.assertEqual(res.json()["role"], "auditor")
        self.assertNotIn("password", res.json())
        self.anon()
        res = self.client.post("/api/auth/login/", {"username": "jane", "password": "Jane-Strong-Pass1"}, format="json")
        self.assertEqual(res.status_code, 200)

    def test_create_validation(self):
        self.as_user(self.admin)
        res = self.client.post("/api/users/", {"username": "TECH1", "password": "short pass", "role": "boss"},
                               format="json")
        self.assertEqual(res.status_code, 400)
        errors = res.json()["errors"]
        self.assertEqual(errors["username"], ["A user with that username already exists."])
        self.assertEqual(errors["password"], ["Password must be 12-128 characters with no spaces."])
        self.assertEqual(errors["role"], ["Role must be admin, auditor or technician."])
        res = self.client.post("/api/users/", {"username": "x", "password": "elevenchars", "role": "auditor"},
                               format="json")
        self.assertIn("username", res.json()["errors"])
        self.assertIn("password", res.json()["errors"])

    def test_patch_rules(self):
        self.as_user(self.admin)
        res = self.client.patch(f"/api/users/{self.tech.id}/", {"username": "x"}, format="json")
        self.assertEqual(res.json()["errors"]["username"], ["Field not editable."])
        res = self.client.patch(f"/api/users/{self.tech.id}/", {"role": "auditor"}, format="json")
        self.assertEqual(res.json()["role"], "auditor")

    def test_deactivate_revokes_token_and_reactivate(self):
        tech_token, _ = Token.objects.get_or_create(user=self.tech)
        self.as_user(self.admin)
        res = self.client.patch(f"/api/users/{self.tech.id}/", {"is_active": False}, format="json")
        self.assertFalse(res.json()["is_active"])
        self.assertFalse(Token.objects.filter(key=tech_token.key).exists())
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {tech_token.key}")
        self.assertEqual(self.client.get("/api/locations/").status_code, 401)
        self.as_user(self.admin)
        self.assertTrue(self.client.patch(f"/api/users/{self.tech.id}/", {"is_active": True},
                                          format="json").json()["is_active"])
        self.assertEqual(self.client.delete(f"/api/users/{self.tech.id}/").status_code, 204)
        self.tech.refresh_from_db()
        self.assertFalse(self.tech.is_active)

    def test_self_protection(self):
        self.as_user(self.admin)
        for method, body in (("patch", {"is_active": False}), ("patch", {"role": "auditor"}), ("delete", None)):
            res = getattr(self.client, method)(f"/api/users/{self.admin.id}/", body, format="json")
            self.assertEqual(res.status_code, 400)
            self.assertEqual(res.json()["code"], "self_action_forbidden")
        res = self.client.post(f"/api/users/{self.admin.id}/set-password/", {"password": "New-Admin-Pass-99"},
                               format="json")
        self.assertEqual(res.status_code, 204)

    def test_admin_can_demote_other_admin_while_another_remains(self):
        other = make_user("admin2", "admin")
        self.as_user(other)
        res = self.client.patch(f"/api/users/{self.admin.id}/", {"role": "technician"}, format="json")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["role"], "technician")

    def test_last_admin_guard(self):
        """Via the API an acting admin always counts, so the self rule normally fires first; the
        last-admin guard is defence in depth (e.g. concurrent demotions). Simulate the race by making
        the 'other active admins' lookup return nothing."""
        target = make_user("admin2", "admin")
        self.as_user(self.admin)
        with mock.patch("accounts.views.active_admin_ids", return_value=set()):
            res = self.client.patch(f"/api/users/{target.id}/", {"role": "auditor"}, format="json")
            self.assertEqual(res.status_code, 400)
            self.assertEqual(res.json()["code"], "last_admin")
            res = self.client.delete(f"/api/users/{target.id}/")
            self.assertEqual(res.json()["code"], "last_admin")
            self.assertEqual(self.client.patch(f"/api/users/{self.tech.id}/", {"role": "auditor"},
                                               format="json").status_code, 200)
        target.refresh_from_db()
        self.assertTrue(target.is_active)
        self.assertEqual(target.profile.role, "admin")

    def test_superuser_counts_as_admin(self):
        root = User.objects.create_superuser("root", password=PW)
        self.as_user(root)
        self.assertEqual(self.client.get("/api/auth/me/").json()["role"], "admin")
        self.assertEqual(self.client.patch(f"/api/users/{self.admin.id}/", {"role": "auditor"},
                                           format="json").status_code, 200)
        res = self.client.patch(f"/api/users/{root.id}/", {"role": "auditor"}, format="json")
        self.assertEqual(res.json()["code"], "self_action_forbidden")

    def test_set_password(self):
        token, _ = Token.objects.get_or_create(user=self.tech)
        self.as_user(self.admin)
        res = self.client.post(f"/api/users/{self.tech.id}/set-password/", {"password": "has space in it"},
                               format="json")
        self.assertEqual(res.status_code, 400)
        res = self.client.post(f"/api/users/{self.tech.id}/set-password/", {"password": "Brand-New-Pass-42"},
                               format="json")
        self.assertEqual(res.status_code, 204)
        self.assertFalse(Token.objects.filter(key=token.key).exists())
        self.tech.refresh_from_db()
        self.assertTrue(self.tech.check_password("Brand-New-Pass-42"))

    def test_user_list_filters(self):
        self.as_user(self.admin)
        res = self.client.get("/api/users/?role=admin").json()
        self.assertEqual([u["username"] for u in res["results"]], ["admin1"])
        self.assertEqual(self.client.get("/api/users/?search=tech").json()["count"], 1)
        self.assertEqual(self.client.get("/api/users/999/").status_code, 404)


ENV = {
    # Empty usernames -> seed falls back to demo_admin / demo_auditor, regardless of the caller's shell.
    "EVAL_ADMIN_USERNAME": "",
    "EVAL_AUDITOR_USERNAME": "",
    "EVAL_ADMIN_PASSWORD": "Seed-Admin-Pass-1",
    "EVAL_AUDITOR_PASSWORD": "Seed-Audit-Pass-1",
    "EVAL_TECHNICIAN_USERNAME": "custom_tech",
    "EVAL_TECHNICIAN_PASSWORD": "Seed-Tech-Pass-1",
}


class SeedTests(APITestCase):
    def test_seed_idempotent(self):
        with mock.patch.dict(os.environ, ENV):
            out1, out2 = StringIO(), StringIO()
            call_command("seed_demo", "--require-accounts", stdout=out1)
            call_command("seed_demo_history", stdout=out1)
            call_command("seed_demo", "--require-accounts", stdout=out2)
            call_command("seed_demo_history", stdout=out2)
        for out in (out1.getvalue(), out2.getvalue()):
            for pw in ENV.values():
                if "Pass" in pw:
                    self.assertNotIn(pw, out)
        self.assertEqual(User.objects.filter(username__in=["demo_admin", "demo_auditor", "custom_tech"]).count(), 3)
        self.assertEqual(User.objects.count(), 3)
        self.assertEqual(Location.objects.count(), 25)
        self.assertEqual(Pallet.objects.count(), 11)
        self.assertEqual(Pallet.objects.filter(status=Pallet.STATUS_ACTIVE).count(), 5)
        self.assertEqual(OperationLog.objects.count(), 17)
        tech = User.objects.get(username="custom_tech")
        self.assertEqual(tech.profile.role, "technician")
        self.assertTrue(tech.check_password("Seed-Tech-Pass-1"))
        res = self.client.post("/api/auth/login/", {"username": "demo_admin", "password": "Seed-Admin-Pass-1"},
                               format="json")
        self.assertEqual(res.json()["user"]["role"], "admin")

    def test_seed_skips_missing_passwords(self):
        clean = {k: v for k, v in os.environ.items() if not k.startswith("EVAL_")}
        with mock.patch.dict(os.environ, clean, clear=True):
            out = StringIO()
            call_command("seed_demo", stdout=out)
        self.assertIn("Skipping demo_admin: EVAL_ADMIN_PASSWORD not set.", out.getvalue())
        self.assertEqual(User.objects.count(), 0)
        self.assertEqual(Pallet.objects.count(), 5)

    def test_hosted_seed_requires_all_account_passwords_before_writing(self):
        from django.core.management.base import CommandError

        clean = {k: v for k, v in os.environ.items() if not k.startswith("EVAL_")}
        with mock.patch.dict(os.environ, clean, clear=True):
            with self.assertRaisesMessage(CommandError, "EVAL_ADMIN_PASSWORD"):
                call_command("seed_demo", "--require-accounts", stdout=StringIO())
        self.assertEqual(User.objects.count(), 0)
        self.assertEqual(Pallet.objects.count(), 0)

    def test_reset_requires_flag(self):
        from django.core.management.base import CommandError

        with self.assertRaises(CommandError):
            call_command("reset_demo", stdout=StringIO())
        with mock.patch.dict(os.environ, ENV):
            call_command("seed_demo", stdout=StringIO())
            call_command("reset_demo", "--yes-i-am-sure", stdout=StringIO())
        self.assertEqual(Pallet.objects.count(), 5)
        self.assertEqual(User.objects.count(), 3)
