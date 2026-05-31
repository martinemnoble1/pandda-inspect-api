"""
The durable data model — the real artifact of this reference.

PanDDA's output is reshaped into a small relational schema. The crucial split:

* immutable scientific artifacts (coords, maps) are *referenced* (Artifact rows
  hold relative paths), never stored in the DB;
* mutable human decision / provenance state lives on Event, where the database
  enforces coherence under concurrent access.

This is what the filesystem-plus-CSV model cannot give you.
"""
from django.db import models


class Project(models.Model):
    """A PanDDA analysis project (one ingested results.json)."""

    name = models.CharField(max_length=255, unique=True)
    # Filesystem location ingested from — the import boundary, not the source
    # of truth once ingested.
    source_root = models.CharField(max_length=1024)
    ingested_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


class Dataset(models.Model):
    """One crystal / dtag within a project, with its analysis metrics."""

    class LigandSource(models.TextChoices):
        # Best-available ligand-spec slot found at ingest, mirroring PanDDA2's
        # own LigandFiles model (cif/pdb/smiles, priority cif>pdb>smiles;
        # verified vs the pandda2 source). Only ``cif`` yields a dictionary
        # usable for refinement/display; the rest record an honest gap (see
        # docs/DESIGN-artifacts-and-jobs.md §6.2). Surfaced so the UI can badge
        # "no restraint dictionary" rather than silently degrade.
        CIF = "cif", "Restraint dictionary (CIF)"
        PDB = "pdb", "Coordinates only (PDB), no dictionary"
        SMILES = "smiles", "SMILES only, no dictionary"
        NONE = "none", "No ligand specification"

    project = models.ForeignKey(
        Project, on_delete=models.CASCADE, related_name="datasets"
    )
    dtag = models.CharField(max_length=64)
    subtitle = models.CharField(max_length=255, blank=True, default="")
    ligand_source = models.CharField(
        max_length=8,
        choices=LigandSource.choices,
        default=LigandSource.NONE,
    )

    analysed_resolution = models.FloatField(null=True, blank=True)
    high_resolution = models.FloatField(null=True, blank=True)
    low_resolution = models.FloatField(null=True, blank=True)
    r_free = models.FloatField(null=True, blank=True)
    r_work = models.FloatField(null=True, blank=True)
    map_uncertainty = models.FloatField(null=True, blank=True)

    # The best model for the WHOLE crystal — a refined pdb (you refine the
    # whole asymmetric unit against one dataset's reflections). Distinct from
    # Event.current_model, which is a ligand built into one event's density.
    # See docs/DESIGN-artifacts-and-jobs.md §1.2.
    current_model = models.ForeignKey(
        "Artifact",
        on_delete=models.SET_NULL,
        related_name="+",
        null=True,
        blank=True,
    )
    # Raised by a re-ingest when the underlying imported bytes changed while
    # current_model points at a human/job artifact — "the analysis under this
    # built model changed; a human should look" (surface, don't resolve, §1.3).
    inputs_changed = models.BooleanField(default=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["project", "dtag"], name="uniq_project_dtag"
            )
        ]
        ordering = ["dtag"]

    def __str__(self):
        return f"{self.project.name}/{self.dtag}"


class Event(models.Model):
    """
    A PanDDA event: a candidate binding site in one dataset.

    The metric fields are analysis output (immutable). The decision fields are
    the mutable human inspection state — exactly what needs a transactional
    home rather than a re-written JSON blob.
    """

    class Decision(models.TextChoices):
        UNREVIEWED = "unreviewed", "Unreviewed"
        HIT = "hit", "Hit"
        NO_HIT = "no_hit", "No hit"
        AMBIGUOUS = "ambiguous", "Ambiguous"

    dataset = models.ForeignKey(
        Dataset, on_delete=models.CASCADE, related_name="events"
    )
    event_num = models.IntegerField()
    site_num = models.IntegerField(null=True, blank=True)

    # --- analysis output (immutable) ---
    event_fraction = models.FloatField(null=True, blank=True)
    bdc = models.FloatField(null=True, blank=True)
    z_peak = models.FloatField(null=True, blank=True)
    z_mean = models.FloatField(null=True, blank=True)
    cluster_size = models.IntegerField(null=True, blank=True)
    map_resolution = models.FloatField(null=True, blank=True)
    # PanDDA2's own ranking output: hit_in_site_probability / Score. This is an
    # *analysis-emitted* confidence, deliberately separate from the mutable
    # human ``decision``/``confidence`` below — the machine's opinion vs the
    # curator's. Null for PanDDA1 ingests, which don't emit it.
    score = models.FloatField(null=True, blank=True)
    # PanDDA's own boolean verdict (PanDDA2 ``interesting`` column). Again
    # distinct from the human decision; advisory only.
    interesting = models.BooleanField(null=True, blank=True)
    # Per-event autobuild metrics from events.yaml ``Build:`` block. The
    # autobuild fits a ligand pose into THIS event's density and scores it; these
    # quantify that fit (build_score/rscc) and the contour at which it reads best
    # (optimal_contour, used to seed the viewer's contour slider). The pose
    # coords themselves are a LIGAND_POSE artifact. All null for PanDDA1 / events
    # without a Build block (~32/200 BAZ2B datasets have none).
    build_score = models.FloatField(null=True, blank=True)
    rscc = models.FloatField(null=True, blank=True)
    optimal_contour = models.FloatField(null=True, blank=True)
    # Recentre target for the viewer.
    xyz_centroid = models.JSONField(default=list)
    xyz_peak = models.JSONField(default=list)

    # --- mutable inspection decision / provenance ---
    decision = models.CharField(
        max_length=16,
        choices=Decision.choices,
        default=Decision.UNREVIEWED,
    )
    confidence = models.CharField(max_length=32, blank=True, default="")
    comment = models.TextField(blank=True, default="")
    inspected_by = models.CharField(max_length=255, blank=True, default="")
    inspected_at = models.DateTimeField(null=True, blank=True)

    # The best model for THIS event's density — a ligand built into the local
    # density (you build per-event, but refine per-crystal; cf.
    # Dataset.current_model). See docs/DESIGN-artifacts-and-jobs.md §1.2.
    current_model = models.ForeignKey(
        "Artifact",
        on_delete=models.SET_NULL,
        related_name="+",
        null=True,
        blank=True,
    )
    # Re-ingest flag, as for Dataset: set when imported bytes changed under a
    # human/job artifact this event points at (surface, don't resolve, §1.3).
    inputs_changed = models.BooleanField(default=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["dataset", "event_num"], name="uniq_dataset_event"
            )
        ]
        ordering = ["dataset__dtag", "event_num"]

    def __str__(self):
        return f"{self.dataset.dtag}:event-{self.event_num}"


class Artifact(models.Model):
    """
    A reference to a large immutable file (coords, MTZ, map, ligand dict).

    The bytes stay on disk (or, later, a blob store); only the typed reference
    lives here. ``relpath`` is relative to the project's source_root.
    """

    class Kind(models.TextChoices):
        STRUCTURE = "structure", "Structure (coords)"
        DATA_MTZ = "data_mtz", "Reflection data (MTZ)"
        OUTPUT_MTZ = "output_mtz", "Output / Z-map MTZ"
        EVENT_MAP = "event_map", "Event map"
        LIGAND = "ligand", "Ligand dictionary"
        # An event's chosen autobuild ligand POSE — ligand-only coords
        # (PanDDA2 autobuild/N_M_ligand_0.pdb). Event-scoped provenance/overlay,
        # NOT a model: never current_model, never refinement input. The model of
        # record is the per-crystal Dataset.current_model (merged pandda-model).
        LIGAND_POSE = "ligand_pose", "Autobuilt ligand pose (event)"
        REPORT_HTML = "report_html", "HTML report"

    class Origin(models.TextChoices):
        # How these bytes came to exist. ``imported`` artifacts are
        # discovered by an ingest reader walking the PanDDA tree;
        # ``built``/``refined`` ones are produced after ingest (a
        # human-built ligand, a refinement job) and are write-once — a new
        # model is a NEW row, never a mutation of an old one.
        IMPORTED = "imported", "Imported (from PanDDA output)"
        BUILT = "built", "Built (human, e.g. ligand placed in Moorhen)"
        REFINED = "refined", "Refined (job output, e.g. giant.quick_refine)"

    # Project-level artifacts (e.g. report HTML) attach to the project with no
    # dataset; dataset/event artifacts set the relevant FK.
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="artifacts",
        null=True,
        blank=True,
    )
    dataset = models.ForeignKey(
        Dataset,
        on_delete=models.CASCADE,
        related_name="artifacts",
        null=True,
        blank=True,
    )
    # Optional link to a specific event (event maps belong to an event).
    event = models.ForeignKey(
        Event,
        on_delete=models.CASCADE,
        related_name="artifacts",
        null=True,
        blank=True,
    )
    kind = models.CharField(max_length=20, choices=Kind.choices)
    relpath = models.CharField(max_length=1024)

    # Embedded bytes, for SMALL structured reference artifacts only —
    # currently ligand restraint dictionaries (~10 KB). Most artifacts (maps,
    # MTZ, coords) stay on disk and leave this blank; the download view streams
    # those by ``relpath``. We embed ligand CIFs because they are small AND
    # live in the original ``data/`` tree OUTSIDE the project source_root
    # (reachable only via absolute symlinks), so a clean relpath can't address
    # them — embedding sidesteps the traversal guard and makes the dict
    # self-contained (survives the data/ tree moving post-ingest). When set,
    # the download view serves this instead of reading ``relpath`` off disk.
    contents = models.TextField(blank=True, default="")

    # --- lineage (see docs/DESIGN-artifacts-and-jobs.md §1) ---
    origin = models.CharField(
        max_length=16, choices=Origin.choices, default=Origin.IMPORTED
    )
    # What these bytes were derived from. The chain
    # ``imported ← built ← refined`` is just ``parent`` links; walking it
    # gives full history, while Event/Dataset.current_model gives "best
    # right now" in one hop. Null for imports (the root of every lineage).
    parent = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        related_name="derived",
        null=True,
        blank=True,
    )
    # The dispatched action that produced these bytes. Null for imports AND
    # for interactive builds (#4 ligand-build is client-side, not a Job) —
    # those are distinguished by ``origin``. Set only for job outputs.
    produced_by = models.ForeignKey(
        "Job",
        on_delete=models.SET_NULL,
        related_name="outputs",
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["kind", "relpath"]

    @property
    def owning_project(self):
        """The Project this artifact belongs to, whether attached directly
        (project-level, e.g. reports) or via its dataset."""
        return self.project or (self.dataset.project if self.dataset else None)

    def __str__(self):
        scope = self.dataset.dtag if self.dataset else "project"
        return f"{scope}:{self.kind}:{self.relpath}"


class Shell(models.Model):
    """Resolution-shell statistics — provenance of the analysis run."""

    project = models.ForeignKey(
        Project, on_delete=models.CASCADE, related_name="shells"
    )
    label = models.CharField(max_length=128)
    resolution_high = models.FloatField(null=True, blank=True)
    resolution_low = models.FloatField(null=True, blank=True)
    map_resolution = models.FloatField(null=True, blank=True)
    map_uncertainty = models.FloatField(null=True, blank=True)
    n_train = models.IntegerField(default=0)
    n_test = models.IntegerField(default=0)

    def __str__(self):
        return f"{self.project.name}/{self.label}"


class Job(models.Model):
    """
    A tracked compute task (refinement, analysis, …) — the single door
    through which job-produced bytes enter the system.

    The API hands a JobSpec to a JobRunner (``jobs.py``) and gets back an
    opaque ``runner_handle`` it polls; on success the output bytes are
    registered as a write-once ``Artifact(produced_by=self)`` and the
    relevant ``current_model`` pointer is repointed. This is what makes
    dispatch consistent with the artifact-tracking model — there is no other
    way for a job to surface output. See docs/DESIGN-artifacts-and-jobs.md §2.
    """

    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        RUNNING = "running", "Running"
        SUCCEEDED = "succeeded", "Succeeded"
        FAILED = "failed", "Failed"

    tool = models.CharField(max_length=64)  # e.g. "giant.quick_refine"
    # Refinement is dataset-scoped (whole crystal); some jobs are event-scoped.
    # Both nullable so the FK granularity matches the tool.
    dataset = models.ForeignKey(
        Dataset,
        on_delete=models.CASCADE,
        related_name="jobs",
        null=True,
        blank=True,
    )
    event = models.ForeignKey(
        Event,
        on_delete=models.CASCADE,
        related_name="jobs",
        null=True,
        blank=True,
    )
    # The JobSpec — WHAT to compute (tool/inputs/params), never WHERE. Paths
    # and scheduler flags belong to the JobRunner, not here (jobs.JobSpec).
    spec = models.JSONField(default=dict)
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.QUEUED
    )
    # Opaque id returned by JobRunner.submit() — a PID, a qsub id, a cloud
    # handle. The API polls JobRunner.status(runner_handle); it never
    # interprets this string.
    runner_handle = models.CharField(max_length=255, blank=True, default="")
    # Set on success. The lineage parent lives on the Artifact (parent FK);
    # this is the job's own pointer to what it produced.
    output_artifact = models.ForeignKey(
        "Artifact",
        on_delete=models.SET_NULL,
        related_name="+",
        null=True,
        blank=True,
    )
    # Relpath (under the project source_root) of the captured stdout/stderr.
    log_relpath = models.CharField(max_length=1024, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        scope = self.dataset.dtag if self.dataset else "project"
        return f"{self.tool}:{scope}:{self.status}"
