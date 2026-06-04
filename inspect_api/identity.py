"""
Map an authenticated request to the curator identity stamped on a decision.

This is the small ``R3`` piece (docs/MATERIA_INTEGRATION.md): when cloud auth is
on (``PANDDA_AUTH_BACKEND=ccp4i2``), a recorded decision should bind to the
*authenticated* user rather than a client-supplied free-text string. The
optional ``ccp4i2-api`` middleware sets the identity attributes on the request;
this module reads ONLY those attributes and imports nothing from that package,
so it is import-safe in the standalone desktop build where the package is not
installed (there it simply returns ``None`` and the legacy client-supplied
``inspected_by`` path is kept).
"""
from typing import Optional, Tuple


def identity_from_request(request) -> Optional[Tuple[str, Optional[str]]]:
    """Return ``(inspected_by, inspected_by_oid)`` for an authenticated request.

    Returns ``None`` when there is no authenticated cloud identity — i.e. the
    no-auth desktop flow, or auth-on but tokenless — in which case the caller
    must NOT overwrite the client-supplied ``inspected_by`` and must leave
    ``inspected_by_oid`` null.

    For the Azure AD path the object id prefers the directory-wide ``oid`` claim
    and falls back to the app-scoped ``sub``. For the local-session / dev-admin
    paths (a genuine authenticated user but no AAD directory id) the email is
    stamped and the oid left null.
    """
    user = getattr(request, "user", None)
    if user is None or not getattr(user, "is_authenticated", False):
        return None

    # The middleware sets its attributes on the underlying Django request; in a
    # DRF view ``request`` is the wrapper, so unwrap to ``_request``.
    django_request = getattr(request, "_request", request)
    claims = getattr(django_request, "azure_ad_claims", None)
    if claims is not None:
        oid = claims.get("oid") or claims.get("sub")
        email = (
            getattr(django_request, "azure_ad_email", None)
            or getattr(user, "email", "")
        )
        return (email or "", oid)

    # Authenticated, but not via Azure AD (local-session token / dev-admin).
    return (getattr(user, "email", "") or "", None)
