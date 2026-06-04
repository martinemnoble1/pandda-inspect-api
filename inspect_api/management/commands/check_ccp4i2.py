"""
Version-fit check for the opt-in ``ccp4i2-api`` cloud-auth dependency.

This is the *first useful output* of wiring CCP4i2 auth (the ``PANDDA_AUTH_BACKEND
=ccp4i2`` path): before that package is load-bearing, confirm it actually fits
the Django / DRF / Python this process is running. Reinspect targets Django 4.2
(to match the Materia/CCP4i2 host); ``ccp4i2-api`` declares its own supported
ranges. This command reads those declared constraints from the installed
package metadata and compares them against what is *actually* importable here,
so any future version skew (e.g. ccp4i2-api re-tightening its Django pin, or a
host on a newer Python) is surfaced loudly instead of failing mysteriously at
request time.

Usage::

    python manage.py check_ccp4i2

Exit status is non-zero if an installed dependency falls outside ccp4i2-api's
declared range, so CI can gate a cloud build on it. If ccp4i2-api is not
installed at all, this is reported and the command exits 0 — the dependency is
opt-in, and absence is not an error for the desktop/standalone build.
"""
import sys
from importlib import metadata

from django.core.management.base import BaseCommand, CommandError

PACKAGE = "ccp4i2-api"

# The runtime deps whose fit actually matters for us (ccp4i2-api also declares
# certifi / PyJWT, but those are its own private concern — we only care about
# the ones we co-own with the host: the web framework + DRF).
DEPS_OF_INTEREST = ("django", "djangorestframework")


class Command(BaseCommand):
    help = (
        "Check that the installed ccp4i2-api fits this Django/DRF/Python "
        "(the opt-in cloud-auth dependency)."
    )

    def handle(self, *args, **options):
        try:
            dist = metadata.distribution(PACKAGE)
        except metadata.PackageNotFoundError:
            self.stdout.write(
                "ccp4i2-api is not installed (cloud auth is opt-in).\n"
                "To enable PANDDA_AUTH_BACKEND=ccp4i2, install it with:\n"
                "    pip install -r requirements-cloud.txt"
            )
            return  # absence is not an error for the standalone build

        self.stdout.write(
            self.style.MIGRATE_HEADING(f"ccp4i2-api {dist.version}")
        )

        # ``packaging`` ships with pip/setuptools and is virtually always
        # present, but it is not a hard dependency of ours — degrade to a
        # report-only mode rather than crash if it is somehow absent.
        try:
            from packaging.requirements import Requirement
            from packaging.specifiers import SpecifierSet
            from packaging.version import Version
        except ImportError:
            self.stdout.write(
                "  (packaging not available — reporting versions only, "
                "cannot verify ranges)"
            )
            Requirement = SpecifierSet = Version = None

        failures = []

        # --- Python -------------------------------------------------------
        requires_python = dist.metadata.get("Requires-Python")
        running_python = ".".join(str(n) for n in sys.version_info[:3])
        failures += self._report(
            "Python",
            running_python,
            requires_python,
            SpecifierSet,
            Version,
        )

        # --- declared runtime deps ---------------------------------------
        declared = {}
        for raw in dist.requires or []:
            if Requirement is None:
                break
            req = Requirement(raw)
            # Skip extras (e.g. the ``test`` extra) — only base runtime deps.
            if req.marker is not None and not req.marker.evaluate():
                continue
            declared[req.name.lower()] = req.specifier

        for name in DEPS_OF_INTEREST:
            try:
                installed = metadata.version(name)
            except metadata.PackageNotFoundError:
                failures.append(f"{name}: NOT INSTALLED")
                self.stdout.write(self.style.ERROR(
                    f"  {name}: FAIL — not installed"
                ))
                continue
            spec = declared.get(name)
            spec_str = str(spec) if spec else None
            failures += self._report(
                name, installed, spec_str, SpecifierSet, Version,
            )

        if failures:
            raise CommandError(
                "ccp4i2-api version-fit FAILED: "
                + "; ".join(failures)
            )
        self.stdout.write(self.style.SUCCESS("version fit OK"))

    def _report(self, label, installed, spec_str, SpecifierSet, Version):
        """Print one PASS/FAIL line; return a list with a failure msg or []."""
        if not spec_str:
            self.stdout.write(
                f"  {label}: {installed} (no declared constraint)"
            )
            return []
        if SpecifierSet is None or Version is None:
            self.stdout.write(
                f"  {label}: {installed} (declares {spec_str}; unverified)"
            )
            return []
        ok = SpecifierSet(spec_str).contains(Version(installed), prereleases=True)
        if ok:
            self.stdout.write(self.style.SUCCESS(
                f"  {label}: {installed} satisfies {spec_str}  PASS"
            ))
            return []
        self.stdout.write(self.style.ERROR(
            f"  {label}: {installed} OUTSIDE {spec_str}  FAIL"
        ))
        return [f"{label} {installed} not in {spec_str}"]
