"""
Ingest a PanDDA results.json (+ optional Projects.csv) into the relational
store. This is the *import boundary*: it runs once per analysis, after which the
API serves SQL, not the filesystem.

Like ``ingest_pandda2``, this command only *parses* results.json into the
normalized ``reconcile.ProjectSpec``; :mod:`inspect_api.reconcile` applies the
re-ingest policy (additive, import-scoped; preserves human decisions and
built/refined models; flags input drift — see
docs/DESIGN-artifacts-and-jobs.md §1.3). Re-running is safe.
"""
import csv
import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from inspect_api.models import Artifact
from inspect_api.reconcile import (
    ArtifactSpec,
    DatasetSpec,
    EventSpec,
    ProjectSpec,
    reconcile_project,
)


class Command(BaseCommand):
    help = "Ingest a PanDDA analysis (results.json) into the database."

    def add_arguments(self, parser):
        parser.add_argument("--project", required=True, help="Project name")
        parser.add_argument(
            "--root",
            required=True,
            help="Path to the project tree (contains pandda/results.json)",
        )

    def handle(self, *args, **opts):
        name = opts["project"]
        root = Path(opts["root"]).expanduser().resolve()
        results_path = root / "pandda" / "results.json"
        if not results_path.is_file():
            raise CommandError(f"No results.json at {results_path}")

        data = json.loads(results_path.read_text())
        spec = self._build_spec(name, root, data)
        res = reconcile_project(spec)

        n_reports = len(spec.artifacts)
        verb = "Ingested" if res.created else "Re-ingested"
        self.stdout.write(
            self.style.SUCCESS(
                f"{verb} '{name}': {res.n_datasets} datasets, "
                f"{res.n_events} events, {res.n_imported_artifacts} imported "
                f"artifacts ({n_reports} reports), {res.n_shells} shells."
            )
        )
        if not res.created:
            self.stdout.write(
                f"  Preserved {res.n_decisions_preserved} human decisions, "
                f"{res.n_built_preserved} built/refined models; "
                f"flagged {res.n_inputs_changed} as inputs_changed."
            )

    def _build_spec(self, name, root, data) -> ProjectSpec:
        """Parse results.json into a normalized ProjectSpec (no DB writes)."""
        subtitles = self._load_subtitles(root)
        records = data.get("dataset_records", {})
        output_files = data.get("output_files", {}).get("dataset_files", {})
        all_dtags = set(output_files) | self._dtags_from_records(records)

        # Events grouped by dtag (results.json lists them flat).
        events_by_dtag = {}
        for ev in data.get("events", []):
            events_by_dtag.setdefault(ev["dtag"], []).append(ev)

        datasets = []
        for dtag in sorted(all_dtags):
            files = output_files.get(dtag, {})
            datasets.append(
                DatasetSpec(
                    dtag=dtag,
                    subtitle=subtitles.get(f"xtal-{dtag}", ""),
                    metrics={
                        "analysed_resolution":
                            self._rec(records, "analysed_resolution", dtag),
                        "high_resolution":
                            self._rec(records, "high_resolution", dtag),
                        "low_resolution":
                            self._rec(records, "low_resolution", dtag),
                        "r_free": self._rec(records, "r_free", dtag),
                        "r_work": self._rec(records, "r_work", dtag),
                        "map_uncertainty":
                            self._rec(records, "map_uncertainty", dtag),
                    },
                    events=self._build_events(events_by_dtag.get(dtag, []),
                                              files),
                    artifacts=self._dataset_artifacts(files),
                )
            )

        return ProjectSpec(
            name=name,
            source_root=str(root),
            datasets=datasets,
            artifacts=self._report_artifacts(root, data),
            shells=self._shells(data),
        )

    def _build_events(self, evs, files) -> list:
        emaps = files.get("event_map_data", {}) or files.get(
            "event_data", {}
        )
        out = []
        for ev in evs:
            num = ev.get("event_num")
            rel = emaps.get(str(num))
            out.append(
                EventSpec(
                    event_num=num,
                    site_num=ev.get("site_num"),
                    metrics={
                        "event_fraction": ev.get("event_fraction"),
                        "bdc": ev.get("bdc"),
                        "z_peak": ev.get("z_peak"),
                        "z_mean": ev.get("z_mean"),
                        "cluster_size": ev.get("cluster_size"),
                        "map_resolution": ev.get("map_resolution"),
                        "xyz_centroid": ev.get("xyz_centroid", []),
                        "xyz_peak": ev.get("xyz_peak", []),
                    },
                    event_map_relpath=self._norm(rel) if rel else None,
                )
            )
        return out

    def _dataset_artifacts(self, files) -> list:
        out = []
        for key, kind in (
            ("structure", Artifact.Kind.STRUCTURE),
            ("data", Artifact.Kind.DATA_MTZ),
            ("output_data", Artifact.Kind.OUTPUT_MTZ),
        ):
            rel = files.get(key)
            if rel:
                out.append(ArtifactSpec(kind, self._norm(rel)))
        for lig in files.get("ligands", []) or []:
            out.append(ArtifactSpec(Artifact.Kind.LIGAND, self._norm(lig)))
        return out

    def _report_artifacts(self, root, data) -> list:
        """Project-level report HTMLs — for the dashboard iframe panel."""
        out = []
        html_map = data.get("output_files", {}).get("html", {}) or {}
        for rel in html_map.values():
            if not rel:
                continue
            norm = self._norm(rel)
            # Some report HTMLs (e.g. pandda_inspect.html) are only written
            # after inspection — don't catalogue ones absent on disk.
            if (root / norm).is_file():
                out.append(ArtifactSpec(Artifact.Kind.REPORT_HTML, norm))
        return out

    def _shells(self, data) -> list:
        out = []
        for sh in data.get("shell_records", []):
            out.append({
                "label": sh.get("label", ""),
                "resolution_high": sh.get("resolution_high"),
                "resolution_low": sh.get("resolution_low"),
                "map_resolution": self._scalar(sh.get("map_resolution")),
                "map_uncertainty": self._scalar(sh.get("map_uncertainty")),
                "n_train": len(sh.get("train", [])),
                "n_test": len(sh.get("test", [])),
            })
        return out

    # --- helpers ---

    @staticmethod
    def _scalar(val):
        """Accept only a scalar number; PanDDA sometimes nests a dtag-keyed
        dict where a single value is expected (e.g. shell map_uncertainty)."""
        return val if isinstance(val, (int, float)) else None

    @staticmethod
    def _rec(records, key, dtag):
        col = records.get(key)
        if isinstance(col, dict):
            val = col.get(dtag)
            # Guard: only accept scalar numbers. Some PanDDA record columns
            # nest a dtag-keyed dict where a per-dataset scalar is expected.
            if isinstance(val, (int, float)):
                return val
        return None

    @staticmethod
    def _dtags_from_records(records):
        dtags = set()
        for col in records.values():
            if isinstance(col, dict):
                dtags.update(col.keys())
        return dtags

    @staticmethod
    def _norm(relpath):
        # Paths in results.json are sometimes 'pandda/...' and sometimes
        # '/processed_datasets/...'. Normalise to project-root-relative.
        rel = relpath.lstrip("/")
        if not rel.startswith("pandda/"):
            rel = "pandda" + ("/" if not rel.startswith("/") else "") + rel.lstrip("/")
        return rel

    @staticmethod
    def _load_subtitles(root):
        csv_path = root / "Projects.csv"
        subtitles = {}
        if csv_path.is_file():
            with open(csv_path, newline="") as fh:
                for row in csv.reader(fh):
                    if len(row) >= 2:
                        subtitles[row[0].strip()] = row[1].strip()
        return subtitles
