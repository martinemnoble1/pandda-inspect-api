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
            return {
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
            name=project_name, source_root=str(dest)
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
