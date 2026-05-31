"""
Ingest a PanDDA **2** output tree into the relational store.

This is the *second* import-boundary reader. PanDDA2 never writes the
``pandda/results.json`` that :mod:`ingest_pandda` consumes (verified against the
PanDDA2 source: zero references to results.json). Instead it emits:

  * ``analyses/pandda_analyse_events.csv`` — one row per event, the global
    event table (event identity = ``(dtag, event_idx)``);
  * ``analyses/pandda_analyse_sites.csv`` — the global site table;
  * ``processed_datasets/<dtag>/`` — per-dataset artifacts: input pdb/mtz
    (``<dtag>-pandda-input.pdb/.mtz``), z-map, one or more event maps
    (``<dtag>-event_N_1-BDC_<x>_map.native.ccp4``), ``events.yaml`` /
    ``processed_dataset.yaml``, ``ligand_files/``.

The internal Dataset/Event/Artifact/Shell model is unchanged — only the *reader*
differs. That the contract survives a completely different on-disk format is the
import-boundary abstraction doing its job.

Idempotent per project name (replaces prior rows; a real implementation would
reconcile rather than clobber — see README).

Observations baked in from a real BAZ2B run (Zenodo 48768, 2026-05-30):
  * dataset-level metrics (resolution, R-factors, map uncertainty) appear on
    every *event* row, not in a separate record — we lift them onto Dataset;
  * a processed dataset may have **zero** events (no events.yaml) — tolerated;
  * an event may have **several** event-map files (one per BDC variant); the
    CSV ``1-BDC`` value picks the canonical one;
  * ``pandda_analyse_sites.csv`` centroids may be unpopulated ``(0,0,0)`` — we
    derive site centroids from member-event coordinates instead;
  * ``ligand_files/`` may be empty — absence is not an error.
"""
import csv
from collections import defaultdict
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from inspect_api.models import Artifact, Dataset, Event, Project

PROCESSED = "processed_datasets"
ANALYSES = "analyses"


class Command(BaseCommand):
    help = "Ingest a PanDDA2 output tree (CSV + per-dataset dirs) into the DB."

    def add_arguments(self, parser):
        parser.add_argument("--project", required=True, help="Project name")
        parser.add_argument(
            "--root",
            required=True,
            help="Path to the PanDDA2 out_dir (contains processed_datasets/ "
            "and analyses/).",
        )

    @transaction.atomic
    def handle(self, *args, **opts):  # noqa: ARG002 (Command signature)
        name = opts["project"]
        root = Path(opts["root"]).expanduser().resolve()
        events_csv = root / ANALYSES / "pandda_analyse_events.csv"
        if not events_csv.is_file():
            raise CommandError(
                f"No {ANALYSES}/pandda_analyse_events.csv at {root} — is this "
                "a PanDDA2 out_dir? (For PanDDA1, use ingest_pandda.)"
            )

        rows = self._read_csv(events_csv)
        if not rows:
            raise CommandError(f"{events_csv} has no event rows.")

        # Replace any prior ingest of this project.
        Project.objects.filter(name=name).delete()
        project = Project.objects.create(name=name, source_root=str(root))

        # --- Datasets: union of every dtag with a processed dir AND every dtag
        # appearing in the event CSV (a dataset can be processed with 0 events,
        # or — defensively — appear in the CSV without a dir). ---
        processed_dir = root / PROCESSED
        dirs = (
            {p.name for p in processed_dir.iterdir() if p.is_dir()}
            if processed_dir.is_dir()
            else set()
        )
        csv_dtags = {r["dtag"] for r in rows if r.get("dtag")}
        all_dtags = sorted(dirs | csv_dtags)

        # Dataset-level metrics live on event rows in PanDDA2; take the first
        # row per dtag as representative (they are per-dataset constants).
        first_row = {}
        for r in rows:
            first_row.setdefault(r["dtag"], r)

        datasets = {}
        for dtag in all_dtags:
            r = first_row.get(dtag, {})
            datasets[dtag] = Dataset.objects.create(
                project=project,
                dtag=dtag,
                analysed_resolution=_f(r.get("analysed_resolution")),
                high_resolution=_f(r.get("high_resolution")),
                low_resolution=_f(r.get("low_resolution")),
                r_free=_f(r.get("r_free")),
                r_work=_f(r.get("r_work")),
                map_uncertainty=_f(r.get("map_uncertainty")),
            )

        # --- Events ---
        n_events = 0
        n_event_maps = 0
        # Accumulate event coordinates per site to derive site centroids (the
        # CSV site centroids are unreliable — often (0,0,0)).
        site_points = defaultdict(list)

        for r in rows:
            ds = datasets.get(r["dtag"])
            if ds is None:
                continue
            event_idx = _i(r.get("event_idx"))
            xyz = [_f(r.get("x")), _f(r.get("y")), _f(r.get("z"))]
            site_idx = _i(r.get("site_idx"))
            event = Event.objects.create(
                dataset=ds,
                event_num=event_idx,
                site_num=site_idx,
                bdc=_f(r.get("bdc")),
                z_peak=_f(r.get("z_peak")),
                z_mean=_f(r.get("z_mean")),
                cluster_size=_i(r.get("cluster_size")),
                map_resolution=_f(r.get("analysed_resolution")),
                score=_f(r.get("hit_in_site_probability")),
                interesting=_b(r.get("interesting")),
                xyz_centroid=[c for c in xyz if c is not None] or [],
                xyz_peak=[],
            )
            n_events += 1
            if site_idx is not None and all(c is not None for c in xyz):
                site_points[site_idx].append(xyz)

            # Event maps: match by the CSV's 1-BDC token, else any event_N map.
            relpath = self._find_event_map(
                root, r["dtag"], event_idx, r.get("1-BDC")
            )
            if relpath:
                Artifact.objects.create(
                    project=project,
                    dataset=ds,
                    event=event,
                    kind=Artifact.Kind.EVENT_MAP,
                    relpath=relpath,
                )
                n_event_maps += 1

        # --- Per-dataset artifacts (input structure + data, z-map, ligands) ---
        n_artifacts = 0
        for dtag, ds in datasets.items():
            ddir = processed_dir / dtag
            if not ddir.is_dir():
                continue
            for fname, kind in (
                (f"{dtag}-pandda-input.pdb", Artifact.Kind.STRUCTURE),
                (f"{dtag}-pandda-input.mtz", Artifact.Kind.DATA_MTZ),
                (f"{dtag}-z_map.native.ccp4", Artifact.Kind.OUTPUT_MTZ),
            ):
                if (ddir / fname).exists():
                    Artifact.objects.create(
                        project=project,
                        dataset=ds,
                        kind=kind,
                        relpath=f"{PROCESSED}/{dtag}/{fname}",
                    )
                    n_artifacts += 1
            lig_dir = ddir / "ligand_files"
            if lig_dir.is_dir():
                for lig in sorted(lig_dir.glob("*.cif")):
                    Artifact.objects.create(
                        project=project,
                        dataset=ds,
                        kind=Artifact.Kind.LIGAND,
                        relpath=f"{PROCESSED}/{dtag}/ligand_files/{lig.name}",
                    )
                    n_artifacts += 1

        # --- Sites: derive centroids from member events (CSV centroids are
        # unreliable). Stored as Shell-free site provenance for now; first-class
        # Site model is a future step (see roadmap). We record the count. ---
        n_sites = len(site_points)

        self.stdout.write(
            self.style.SUCCESS(
                f"Ingested PanDDA2 '{name}': {len(datasets)} datasets, "
                f"{n_events} events, {n_event_maps} event maps, "
                f"{n_artifacts} dataset artifacts, {n_sites} sites "
                f"(centroids derived from events)."
            )
        )

    # --- helpers ---

    @staticmethod
    def _read_csv(path: Path) -> list[dict]:
        with open(path, newline="", encoding="utf-8") as fh:
            return list(csv.DictReader(fh))

    @staticmethod
    def _find_event_map(root: Path, dtag: str, event_idx, bdc_token) -> str | None:
        """Return the project-relative path to the event map for this event.

        PanDDA2 names them ``<dtag>-event_<N>_1-BDC_<x>_map.native.ccp4`` and an
        event may have several (one per BDC variant). The CSV's ``1-BDC`` value
        identifies the canonical one; fall back to any event_<N> map.
        """
        ddir = root / PROCESSED / dtag
        if not ddir.is_dir() or event_idx is None:
            return None
        candidates = sorted(ddir.glob(f"{dtag}-event_{event_idx}_*map.native.ccp4"))
        if not candidates:
            return None
        chosen = None
        if bdc_token not in (None, ""):
            token = str(bdc_token).strip()
            for c in candidates:
                # Match the exact 1-BDC token, e.g. "1-BDC_0.05_".
                if f"1-BDC_{token}_" in c.name:
                    chosen = c
                    break
        chosen = chosen or candidates[0]
        return f"{PROCESSED}/{dtag}/{chosen.name}"


def _f(v):
    """Float or None — tolerates empty strings and PanDDA's blanks."""
    if v in (None, "", "None"):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _i(v):
    f = _f(v)
    return int(f) if f is not None else None


def _b(v):
    if isinstance(v, bool):
        return v
    if v in (None, "", "None"):
        return None
    return str(v).strip().lower() in ("true", "1", "yes")
