"""
Zip import: the write side of the import boundary.

Accepts an uploaded zip in one of two flavours and lands it as a project under
PANDDA_DATA_ROOT, then ingests it into the relational store:

  (a) a zipped PanDDA *output* directory  — detected by a `pandda/results.json`
      somewhere inside the archive;
  (b) a zipped *crystals* directory + manifest — a `manifest.(csv|json)` plus
      per-crystal subdirectories. (Reference scope: we land it and record the
      datasets from the manifest; running pandda.analyse on it is a JobRunner
      concern, stubbed.)

This is deliberately filesystem-landing + ingest, mirroring how PanDDA itself
produces a tree: the zip is an input adapter, the DB is the source of truth
afterwards.
"""
import csv
import json
import shutil
import tempfile
import zipfile
from pathlib import Path

from django.conf import settings
from django.core.management import call_command

from .models import Dataset, Project


class ImportError_(Exception):
    pass


def _find_pandda_root(extracted: Path) -> Path | None:
    """Return the dir whose child is `pandda/results.json`, if any."""
    for results in extracted.rglob("pandda/results.json"):
        return results.parent.parent
    return None


def _find_manifest(extracted: Path) -> Path | None:
    for name in ("manifest.csv", "manifest.json", "Projects.csv"):
        for m in extracted.rglob(name):
            return m
    return None


def detect_flavour(extracted: Path) -> str:
    if _find_pandda_root(extracted) is not None:
        return "pandda-output"
    if _find_manifest(extracted) is not None:
        return "crystals-manifest"
    raise ImportError_(
        "Unrecognised zip: expected either a pandda/results.json (PanDDA "
        "output) or a manifest.(csv|json) (crystals directory)."
    )


def _is_pandda2_root(root: Path) -> bool:
    """PanDDA2 out_dir = analyses/pandda_analyse_events.csv present."""
    return (root / "analyses" / "pandda_analyse_events.csv").is_file()


def detect_pandda_flavour(root: Path) -> str:
    """Classify a PanDDA *output directory* (already on disk, not a zip).

    Returns ``"pandda2"`` (the CSV-based format) or ``"pandda1"`` (the
    ``pandda/results.json`` format). Raises if neither marker is present —
    so a mistaken folder pick fails loudly rather than ingesting nothing.
    """
    if _is_pandda2_root(root):
        return "pandda2"
    if (root / "pandda" / "results.json").is_file() or (
        root / "results.json"
    ).is_file():
        return "pandda1"
    raise ImportError_(
        f"{root} is not a PanDDA output directory: expected "
        "analyses/pandda_analyse_events.csv (PanDDA2) or "
        "pandda/results.json (PanDDA1)."
    )


def ingest_path(source_dir: Path, project_name: str) -> dict:
    """Ingest a PanDDA output directory **in place** (no copy).

    Unlike :func:`import_zip`, this points the project's ``source_root`` at
    ``source_dir`` exactly where it already lives — the affordance the spec
    calls out as Electron/CLI-only (a browser sandbox can never hand the
    server a directory path). Artifact serving resolves relpaths against
    ``source_root`` (storage.LocalFileStore), so nothing is duplicated.

    The caller is responsible for the trust boundary: this runs ingest against
    an arbitrary server-side path, so the HTTP entry point MUST be restricted
    to localhost (the Electron/CLI binding) — see views.ingest_path_.
    """
    root = Path(source_dir).expanduser().resolve()
    if not root.is_dir():
        raise ImportError_(f"Not a directory: {root}")
    if Project.objects.filter(name=project_name).exists():
        raise ImportError_(f"Project '{project_name}' already exists.")

    flavour = detect_pandda_flavour(root)
    command = "ingest_pandda2" if flavour == "pandda2" else "ingest_pandda"
    # Both readers set Project.source_root = root (in place); reconcile owns
    # persistence. No shutil.copytree — that's the whole point.
    call_command(command, project=project_name, root=str(root))
    project = Project.objects.get(name=project_name)
    return {
        "id": project.id,
        "flavour": flavour,
        "project": project_name,
        "source_root": str(root),
        "n_datasets": project.datasets.count(),
        "copied": False,
    }


def import_zip(zip_path: Path, project_name: str) -> dict:
    """Extract, detect flavour, land under PANDDA_DATA_ROOT, ingest. Returns a
    summary dict."""
    data_root = Path(settings.PANDDA_DATA_ROOT)
    data_root.mkdir(parents=True, exist_ok=True)
    dest = data_root / project_name
    if dest.exists():
        raise ImportError_(f"Project '{project_name}' already exists.")

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        with zipfile.ZipFile(zip_path) as zf:
            # Guard against zip-slip.
            for member in zf.namelist():
                target = (tmp_path / member).resolve()
                if not str(target).startswith(str(tmp_path.resolve())):
                    raise ImportError_(f"Unsafe path in zip: {member}")
            zf.extractall(tmp_path)

        flavour = detect_flavour(tmp_path)

        if flavour == "pandda-output":
            root = _find_pandda_root(tmp_path)
            shutil.copytree(root, dest)
            call_command(
                "ingest_pandda", project=project_name, root=str(dest)
            )
            project = Project.objects.get(name=project_name)
            # We COPIED the tree under PANDDA_DATA_ROOT, so we own it — mark it
            # purge-deletable (ingest_path leaves this False). See
            # docs/DELETION_AND_CLEANUP.md §4 correction 4.
            project.source_managed = True
            project.save(update_fields=["source_managed"])
            return {
                "id": project.id,
                "flavour": flavour,
                "project": project_name,
                "n_datasets": project.datasets.count(),
            }

        # crystals-manifest: land the tree and register datasets from the
        # manifest. (Analysis itself is a JobRunner concern — out of scope for
        # this thin reference.)
        manifest = _find_manifest(tmp_path)
        crystals_root = manifest.parent
        shutil.copytree(crystals_root, dest)
        project = Project.objects.create(
            name=project_name, source_root=str(dest), source_managed=True
        )
        rows = _read_manifest(dest / manifest.name)
        for row in rows:
            dtag = row.get("dtag") or row.get("crystal") or row.get("name")
            if dtag:
                Dataset.objects.get_or_create(
                    project=project,
                    dtag=str(dtag),
                    defaults={"subtitle": row.get("subtitle", "")},
                )
        return {
            "id": project.id,
            "flavour": flavour,
            "project": project_name,
            "n_datasets": project.datasets.count(),
            "note": "Crystals registered from manifest; run analysis via a "
            "JobRunner (stubbed in this reference).",
        }


def _read_manifest(path: Path) -> list[dict]:
    if path.suffix == ".json":
        data = json.loads(path.read_text())
        return data if isinstance(data, list) else data.get("crystals", [])
    # CSV (incl. Projects.csv): first row may be header.
    rows = []
    with open(path, newline="") as fh:
        reader = csv.reader(fh)
        first = next(reader, None)
        if not first:
            return rows
        header = [h.strip().lower() for h in first]
        has_header = any(h in ("dtag", "crystal", "name") for h in header)
        if has_header:
            idx = {h: i for i, h in enumerate(header)}
            key = next(
                (k for k in ("dtag", "crystal", "name") if k in idx), None
            )
            for r in reader:
                if r:
                    rows.append(
                        {
                            "dtag": r[idx[key]],
                            "subtitle": r[idx["subtitle"]]
                            if "subtitle" in idx and len(r) > idx["subtitle"]
                            else "",
                        }
                    )
        else:
            # Headerless: assume col0=dtag, col1=subtitle (Projects.csv style).
            for r in [first] + list(reader):
                if r:
                    rows.append(
                        {"dtag": r[0], "subtitle": r[1] if len(r) > 1 else ""}
                    )
    return rows
