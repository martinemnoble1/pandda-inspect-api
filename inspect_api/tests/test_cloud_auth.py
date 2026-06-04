"""
Opt-in cloud auth (PANDDA_AUTH_BACKEND=ccp4i2) and identity stamping.

Two guarantees are under test:

1. **Zero-delta default** — with the flag unset (the standalone Electron
   desktop build, the principal delivery mode), the auth machinery is entirely
   absent: no ccp4i2 middleware, no ``django.contrib.auth``, no DRF auth or
   permission classes. Nothing can challenge the tokenless desktop client.
2. **Identity stamping (R3)** — when an authenticated request records a
   decision, ``inspected_by`` / ``inspected_by_oid`` bind to the AAD identity
   (oid preferred, sub fallback); with no auth, the client-supplied
   ``inspected_by`` stands and the oid stays null.
"""
import json
import os
import subprocess
import sys
import unittest
from types import SimpleNamespace

from django.conf import settings
from django.test import SimpleTestCase, TestCase

from inspect_api.identity import identity_from_request
from inspect_api.models import Dataset, Event, Project
from inspect_api.serializers import EventSerializer
from inspect_api.views import EventViewSet


def _ccp4i2_available() -> bool:
    try:
        import ccp4i2_api  # noqa: F401
    except ImportError:
        return False
    return True


class AuthDefaultsTest(SimpleTestCase):
    """The flag-unset default must be byte-identical to the pre-auth app."""

    def test_default_is_zero_delta(self):
        self.assertNotIn("ccp4i2", "\n".join(settings.MIDDLEWARE))
        self.assertNotIn("django.contrib.auth", settings.INSTALLED_APPS)
        self.assertEqual(
            settings.REST_FRAMEWORK["DEFAULT_AUTHENTICATION_CLASSES"], []
        )
        self.assertEqual(
            settings.REST_FRAMEWORK["DEFAULT_PERMISSION_CLASSES"], []
        )


@unittest.skipUnless(_ccp4i2_available(), "ccp4i2-api not installed")
class AuthEnabledSettingsTest(SimpleTestCase):
    """With the flag on, exactly one middleware is selected by env shape, the
    DRF auth class is wired, and NO global permission is added."""

    def _resolved(self, **env):
        code = (
            "import django, json; django.setup();"
            "from django.conf import settings;"
            "print(json.dumps({"
            "'mw': list(settings.MIDDLEWARE),"
            "'apps': list(settings.INSTALLED_APPS),"
            "'auth': settings.REST_FRAMEWORK"
            "['DEFAULT_AUTHENTICATION_CLASSES'],"
            "'perms': settings.REST_FRAMEWORK"
            "['DEFAULT_PERMISSION_CLASSES']}))"
        )
        child_env = {
            **os.environ,
            "DJANGO_SETTINGS_MODULE": "config.settings",
            **env,
        }
        out = subprocess.check_output(
            [sys.executable, "-c", code],
            env=child_env,
            cwd=str(settings.BASE_DIR),
        )
        return json.loads(out.decode())

    def test_dev_admin_selected_in_debug(self):
        r = self._resolved(PANDDA_AUTH_BACKEND="ccp4i2")
        self.assertIn(
            "ccp4i2_api.middleware.DevAdminMiddleware", r["mw"]
        )
        self.assertIn("django.contrib.auth", r["apps"])
        self.assertEqual(
            r["auth"], ["ccp4i2_api.drf.AzureADAuthentication"]
        )
        # No global enforcement — protects the desktop flow.
        self.assertEqual(r["perms"], [])

    def test_azure_ad_selected_when_required(self):
        r = self._resolved(
            PANDDA_AUTH_BACKEND="ccp4i2", CCP4I2_REQUIRE_AUTH="true"
        )
        self.assertIn(
            "ccp4i2_api.middleware.AzureADAuthMiddleware", r["mw"]
        )

    def test_local_session_selected_when_token_set(self):
        # CCP4I2_REQUIRE_AUTH unset, token present -> desktop loopback path.
        r = self._resolved(
            PANDDA_AUTH_BACKEND="ccp4i2",
            CCP4I2_REQUIRE_AUTH="",
            CCP4I2_LOCAL_SESSION_TOKEN="secret",
        )
        self.assertIn(
            "ccp4i2_api.middleware.LocalSessionAuthMiddleware", r["mw"]
        )


class IdentityFromRequestTest(SimpleTestCase):
    """Pure-logic coverage of the request -> (inspected_by, oid) mapping."""

    def test_none_when_no_user(self):
        self.assertIsNone(
            identity_from_request(SimpleNamespace(user=None))
        )

    def test_none_when_unauthenticated(self):
        req = SimpleNamespace(
            user=SimpleNamespace(is_authenticated=False)
        )
        self.assertIsNone(identity_from_request(req))

    def test_oid_preferred_over_sub(self):
        req = SimpleNamespace(
            user=SimpleNamespace(is_authenticated=True, email="u@x"),
            _request=SimpleNamespace(
                azure_ad_claims={"oid": "OID", "sub": "SUB"},
                azure_ad_email="u@x",
            ),
        )
        self.assertEqual(identity_from_request(req), ("u@x", "OID"))

    def test_sub_fallback_when_no_oid(self):
        req = SimpleNamespace(
            user=SimpleNamespace(is_authenticated=True, email="u@x"),
            _request=SimpleNamespace(
                azure_ad_claims={"sub": "SUB"}, azure_ad_email=None
            ),
        )
        self.assertEqual(identity_from_request(req), ("u@x", "SUB"))

    def test_local_session_user_has_no_oid(self):
        # Authenticated (e.g. local-session/dev-admin) but no AAD claims:
        # stamp the email, leave the oid null.
        req = SimpleNamespace(
            user=SimpleNamespace(is_authenticated=True, email="os@host"),
            _request=SimpleNamespace(),
        )
        self.assertEqual(
            identity_from_request(req), ("os@host", None)
        )


class IdentityStampingTest(TestCase):
    """End-to-end through EventViewSet.perform_update."""

    def setUp(self):
        self.project = Project.objects.create(
            name="P", source_root="/tmp/none"
        )
        self.dataset = Dataset.objects.create(
            project=self.project, dtag="d1"
        )
        self.event = Event.objects.create(
            dataset=self.dataset, event_num=1
        )

    def _update(self, request_stub, data):
        view = EventViewSet()
        view.request = request_stub
        serializer = EventSerializer(
            instance=self.event, data=data, partial=True
        )
        serializer.is_valid(raise_exception=True)
        view.perform_update(serializer)
        self.event.refresh_from_db()

    def test_no_auth_keeps_client_inspected_by(self):
        self._update(
            SimpleNamespace(user=None),
            {"decision": "hit", "inspected_by": "alice"},
        )
        self.assertEqual(self.event.decision, "hit")
        self.assertEqual(self.event.inspected_by, "alice")
        self.assertIsNone(self.event.inspected_by_oid)

    def test_aad_overrides_with_oid_and_email(self):
        stub = SimpleNamespace(
            user=SimpleNamespace(is_authenticated=True, email="u@x"),
            _request=SimpleNamespace(
                azure_ad_claims={"oid": "OID-1", "sub": "SUB-1"},
                azure_ad_email="u@x",
            ),
        )
        # A client-supplied value must NOT win over the authenticated identity.
        self._update(stub, {"decision": "hit", "inspected_by": "spoof"})
        self.assertEqual(self.event.inspected_by, "u@x")
        self.assertEqual(self.event.inspected_by_oid, "OID-1")

    def test_aad_sub_fallback(self):
        stub = SimpleNamespace(
            user=SimpleNamespace(is_authenticated=True, email="u@x"),
            _request=SimpleNamespace(
                azure_ad_claims={"sub": "SUB-1"}, azure_ad_email=None
            ),
        )
        self._update(stub, {"decision": "hit"})
        self.assertEqual(self.event.inspected_by_oid, "SUB-1")
