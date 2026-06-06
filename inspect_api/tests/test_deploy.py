"""
Deployment enablement: health probe + DB default.

Cheap guards that the container surface works and that the desktop/dev DB
default is unchanged when DATABASE_URL is unset.
"""
from django.conf import settings
from django.test import SimpleTestCase, TestCase
from rest_framework.test import APIClient


class HealthTest(TestCase):
    def setUp(self):
        self.client = APIClient()

    def test_healthz_ok(self):
        resp = self.client.get("/healthz")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"status": "ok"})

    def test_api_health_alias_ok(self):
        resp = self.client.get("/api/v1/health/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "ok")


class DatabaseDefaultTest(SimpleTestCase):
    def test_sqlite_default_when_no_database_url(self):
        # The test run sets no DATABASE_URL, so the engine must be the
        # unchanged SQLite default (the desktop/dev binding).
        self.assertTrue(
            settings.DATABASES["default"]["ENGINE"].endswith("sqlite3")
        )
