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

This command's job is purely to parse the PanDDA2 tree into the normalized
``reconcile.ProjectSpec``; :mod:`inspect_api.reconcile` then applies the
re-ingest policy (additive, import-scoped; preserves human decisions and
built/refined models; flags input drift — see
docs/DESIGN-artifacts-and-jobs.md §1.3). Re-running is therefore safe: it
no longer clobbers decision state.

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

from inspect_api.models import Artifact
from inspect_api.reconcile import (
    ArtifactSpec,
    DatasetSpec,
    EventSpec,
    ProjectSpec,
    reconcile_project,
)

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

        spec = self._build_spec(name, root, rows)
        res = reconcile_project(spec)

        n_event_maps = sum(
            1 for d in spec.datasets for e in d.events if e.event_map_relpath
        )
        verb = "Ingested" if res.created else "Re-ingested"
        self.stdout.write(
            self.style.SUCCESS(
                f"{verb} PanDDA2 '{name}': {res.n_datasets} datasets, "
                f"{res.n_events} events, {n_event_maps} event maps, "
                f"{res.n_imported_artifacts} imported artifacts."
            )
        )
        if not res.created:
            self.stdout.write(
                f"  Preserved {res.n_decisions_preserved} human decisions, "
                f"{res.n_built_preserved} built/refined models; "
                f"flagged {res.n_inputs_changed} as inputs_changed."
            )

    def _build_spec(self, name, root, rows) -> ProjectSpec:
        """Parse the PanDDA2 tree into a normalized ProjectSpec.

        No DB writes here — the import boundary's only job is to turn the
        on-disk format into the shared spec; reconcile.py owns persistence.
        """
        # --- Datasets: union of every dtag with a processed dir AND every
        # dtag in the event CSV (a dataset can be processed with 0 events, or
        # — defensively — appear in the CSV without a dir). ---
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

        # Group event rows by dtag.
        rows_by_dtag = defaultdict(list)
        for r in rows:
            rows_by_dtag[r["dtag"]].append(r)

        datasets = []
        for dtag in all_dtags:
            r0 = first_row.get(dtag, {})
            ds_spec = DatasetSpec(
                dtag=dtag,
                metrics={
                    "analysed_resolution": _f(r0.get("analysed_resolution")),
                    "high_resolution": _f(r0.get("high_resolution")),
                    "low_resolution": _f(r0.get("low_resolution")),
                    "r_free": _f(r0.get("r_free")),
                    "r_work": _f(r0.get("r_work")),
                    "map_uncertainty": _f(r0.get("map_uncertainty")),
                },
                events=self._build_events(root, dtag, rows_by_dtag[dtag]),
                artifacts=self._dataset_artifacts(processed_dir, dtag),
                current_model_relpath=self._analysis_model_relpath(
                    processed_dir, dtag
                ),
            )
            datasets.append(ds_spec)

        return ProjectSpec(
            name=name, source_root=str(root), datasets=datasets
        )

    def _build_events(self, root, dtag, rows) -> list:
        events = []
        for r in rows:
            event_idx = _i(r.get("event_idx"))
            xyz = [_f(r.get("x")), _f(r.get("y")), _f(r.get("z"))]
            events.append(
                EventSpec(
                    event_num=event_idx,
                    site_num=_i(r.get("site_idx")),
                    metrics={
                        "bdc": _f(r.get("bdc")),
                        "z_peak": _f(r.get("z_peak")),
                        "z_mean": _f(r.get("z_mean")),
                        "cluster_size": _i(r.get("cluster_size")),
                        "map_resolution": _f(r.get("analysed_resolution")),
                        "score": _f(r.get("hit_in_site_probability")),
                        "interesting": _b(r.get("interesting")),
                        "xyz_centroid": [c for c in xyz if c is not None]
                        or [],
                        "xyz_peak": [],
                    },
                    event_map_relpath=self._find_event_map(
                        root, dtag, event_idx, r.get("1-BDC")
                    ),
                )
            )
        return events

    @staticmethod
    def _dataset_artifacts(processed_dir, dtag) -> list:
        ddir = processed_dir / dtag
        if not ddir.is_dir():
            return []
        out = []
        for fname, kind in (
            (f"{dtag}-pandda-input.pdb", Artifact.Kind.STRUCTURE),
            (f"{dtag}-pandda-input.mtz", Artifact.Kind.DATA_MTZ),
            (f"{dtag}-z_map.native.ccp4", Artifact.Kind.OUTPUT_MTZ),
        ):
            if (ddir / fname).exists():
                out.append(
                    ArtifactSpec(kind, f"{PROCESSED}/{dtag}/{fname}")
                )
        # Ligand restraint dictionary. PanDDA2's ligand_files/ is often empty;
        # the canonical CIF lives in the ORIGINAL data tree at
        # data/<dtag>/ligand.cif, reachable by resolving the -pandda-input.pdb
        # symlink (which points into data/<dtag>/). It's outside source_root,
        # so we EMBED its bytes, not a path (see Artifact.contents
        # + docs). Falls back to any ligand_files/*.cif if present.
        cif = Command._find_ligand_cif(ddir, dtag)
        if cif is not None:
            out.append(
                ArtifactSpec(
                    Artifact.Kind.LIGAND,
                    relpath=f"{PROCESSED}/{dtag}/ligand.cif",
                    contents=cif,
                )
            )
        # The analysis's merged model (autobuild). A STRUCTURE artifact, but
        # origin=imported (re-derivable analysis output). Catalogued here so
        # the download view can serve it; set as current_model in reconcile so
        # the viewer loads the built ligand. Distinct from the apo input pdb.
        model_rel = Command._analysis_model_relpath(processed_dir, dtag)
        if model_rel:
            out.append(ArtifactSpec(Artifact.Kind.STRUCTURE, model_rel))
        return out

    @staticmethod
    def _analysis_model_relpath(processed_dir, dtag) -> str | None:
        """Relpath of PanDDA2's merged autobuild model, if present.

        ``modelled_structures/<dtag>-pandda-model.pdb`` — protein + built
        ligand(s). Absent for ~1/6 of BAZ2B datasets (no autobuild); None then.
        """
        rel = f"{PROCESSED}/{dtag}/modelled_structures/{dtag}-pandda-model.pdb"
        return rel if (processed_dir / dtag / "modelled_structures"
                       / f"{dtag}-pandda-model.pdb").exists() else None

    @staticmethod
    def _find_ligand_cif(ddir: Path, dtag: str) -> str | None:
        """Return the ligand restraint CIF *contents*, or None.

        Looks first in the original data tree (data/<dtag>/ligand.cif), located
        by resolving the -pandda-input.pdb symlink to its data/<dtag>/ dir; then
        falls back to any ligand_files/*.cif inside the processed dir. Returns
        the file text (embedded in the DB). Tolerates SMILES-only / missing
        dicts by returning None — those datasets are flagged by the caller.
        """
        candidates = []
        # 1. data/<dtag>/ligand.cif via the resolved input-pdb symlink.
        input_pdb = ddir / f"{dtag}-pandda-input.pdb"
        try:
            data_dir = input_pdb.resolve().parent
            candidates.append(data_dir / "ligand.cif")
        except OSError:
            pass
        # 2. Fallback: ligand_files/*.cif inside the processed dataset dir.
        lig_dir = ddir / "ligand_files"
        if lig_dir.is_dir():
            candidates.extend(sorted(lig_dir.glob("*.cif")))
        for cif in candidates:
            try:
                if cif.is_file():
                    return cif.read_text(encoding="utf-8")
            except OSError:
                continue
        return None

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
