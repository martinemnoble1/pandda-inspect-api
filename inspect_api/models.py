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

    project = models.ForeignKey(
        Project, on_delete=models.CASCADE, related_name="datasets"
    )
    dtag = models.CharField(max_length=64)
    subtitle = models.CharField(max_length=255, blank=True, default="")

    analysed_resolution = models.FloatField(null=True, blank=True)
    high_resolution = models.FloatField(null=True, blank=True)
    low_resolution = models.FloatField(null=True, blank=True)
    r_free = models.FloatField(null=True, blank=True)
    r_work = models.FloatField(null=True, blank=True)
    map_uncertainty = models.FloatField(null=True, blank=True)

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
        REPORT_HTML = "report_html", "HTML report"

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
