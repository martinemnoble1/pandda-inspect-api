"""
Ingest a PanDDA results.json (+ optional Projects.csv) into the relational
store. This is the *import boundary*: it runs once per analysis, after which the
API serves SQL, not the filesystem.

Idempotent: re-running for the same project name replaces its rows but
preserves nothing of the old decision state (this is a reference — a real
implementation would reconcile, not clobber; see the README's note on the
reconciliation problem).
"""
import csv
import json
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from inspect_api.models import Artifact, Dataset, Event, Project, Shell


class Command(BaseCommand):
    help = "Ingest a PanDDA analysis (results.json) into the database."

    def add_arguments(self, parser):
        parser.add_argument("--project", required=True, help="Project name")
        parser.add_argument(
            "--root",
            required=True,
            help="Path to the project tree (contains pandda/results.json)",
        )

    @transaction.atomic
    def handle(self, *args, **opts):
        name = opts["project"]
        root = Path(opts["root"]).expanduser().resolve()
        results_path = root / "pandda" / "results.json"
        if not results_path.is_file():
            raise CommandError(f"No results.json at {results_path}")

        data = json.loads(results_path.read_text())

        # Replace any prior ingest of this project.
        Project.objects.filter(name=name).delete()
        project = Project.objects.create(name=name, source_root=str(root))

        subtitles = self._load_subtitles(root)
        dataset_records = data.get("dataset_records", {})
        output_files = data.get("output_files", {}).get("dataset_files", {})
        all_dtags = set(output_files) | self._dtags_from_records(dataset_records)

        datasets = {}
        for dtag in sorted(all_dtags):
            datasets[dtag] = Dataset.objects.create(
                project=project,
                dtag=dtag,
                subtitle=subtitles.get(f"xtal-{dtag}", ""),
                analysed_resolution=self._rec(dataset_records, "analysed_resolution", dtag),
                high_resolution=self._rec(dataset_records, "high_resolution", dtag),
                low_resolution=self._rec(dataset_records, "low_resolution", dtag),
                r_free=self._rec(dataset_records, "r_free", dtag),
                r_work=self._rec(dataset_records, "r_work", dtag),
                map_uncertainty=self._rec(dataset_records, "map_uncertainty", dtag),
            )

        n_events = 0
        for ev in data.get("events", []):
            dtag = ev["dtag"]
            ds = datasets.get(dtag)
            if ds is None:
                continue
            event = Event.objects.create(
                dataset=ds,
                event_num=ev.get("event_num"),
                site_num=ev.get("site_num"),
                event_fraction=ev.get("event_fraction"),
                bdc=ev.get("bdc"),
                z_peak=ev.get("z_peak"),
                z_mean=ev.get("z_mean"),
                cluster_size=ev.get("cluster_size"),
                map_resolution=ev.get("map_resolution"),
                xyz_centroid=ev.get("xyz_centroid", []),
                xyz_peak=ev.get("xyz_peak", []),
            )
            n_events += 1
            # Event maps are keyed by event_num within the dataset's files.
            files = output_files.get(dtag, {})
            emaps = files.get("event_map_data", {}) or files.get("event_data", {})
            relpath = emaps.get(str(ev.get("event_num")))
            if relpath:
                Artifact.objects.create(
                    dataset=ds,
                    event=event,
                    kind=Artifact.Kind.EVENT_MAP,
                    relpath=self._norm(relpath),
                )

        n_artifacts = 0
        for dtag, files in output_files.items():
            ds = datasets.get(dtag)
            if ds is None:
                continue
            for key, kind in (
                ("structure", Artifact.Kind.STRUCTURE),
                ("data", Artifact.Kind.DATA_MTZ),
                ("output_data", Artifact.Kind.OUTPUT_MTZ),
            ):
                rel = files.get(key)
                if rel:
                    Artifact.objects.create(
                        dataset=ds, kind=kind, relpath=self._norm(rel)
                    )
                    n_artifacts += 1
            for lig in files.get("ligands", []) or []:
                Artifact.objects.create(
                    dataset=ds,
                    kind=Artifact.Kind.LIGAND,
                    relpath=self._norm(lig),
                )
                n_artifacts += 1

        n_shells = 0
        for sh in data.get("shell_records", []):
            Shell.objects.create(
                project=project,
                label=sh.get("label", ""),
                resolution_high=sh.get("resolution_high"),
                resolution_low=sh.get("resolution_low"),
                map_resolution=sh.get("map_resolution"),
                map_uncertainty=sh.get("map_uncertainty"),
                n_train=len(sh.get("train", [])),
                n_test=len(sh.get("test", [])),
            )
            n_shells += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Ingested '{name}': {len(datasets)} datasets, {n_events} "
                f"events, {n_artifacts} artifacts, {n_shells} shells."
            )
        )

    # --- helpers ---

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
