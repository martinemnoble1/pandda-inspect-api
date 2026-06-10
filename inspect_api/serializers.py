from django.conf import settings
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from .models import (
    Artifact,
    Dataset,
    Event,
    Finding,
    Job,
    Project,
    Run,
    Shell,
)


class ArtifactSerializer(serializers.ModelSerializer):
    # A client-resolvable URL to stream the bytes through the API.
    download_url = serializers.SerializerMethodField()

    class Meta:
        model = Artifact
        fields = [
            "id", "kind", "relpath", "project", "dataset", "event",
            "download_url",
            # Explicit map-coefficient columns for MTZ artifacts (the client
            # computes maps with these labels — no Coot column heuristics).
            "map_columns",
        ]

    @extend_schema_field(serializers.CharField())
    def get_download_url(self, obj):
        return f"/api/v1/artifacts/{obj.id}/download/"


class EventSerializer(serializers.ModelSerializer):
    dtag = serializers.CharField(source="dataset.dtag", read_only=True)
    # Ligand-spec provenance (cif|pdb|smiles|none) from the dataset, so the
    # inspect view can honestly badge "no restraint dictionary" when != cif.
    ligand_source = serializers.CharField(
        source="dataset.ligand_source", read_only=True
    )
    # Everything the inspect client needs to load this event in one place: the
    # event's own artifacts (event map) PLUS its dataset's shared artifacts
    # (structure, ligand dicts). The structure attaches at dataset level (one
    # model per crystal, shared across its events), so surface it here too.
    artifacts = serializers.SerializerMethodField()
    # The coordinates the viewer should load: the event's own current_model
    # (a built ligand, event-scoped) if set, else the dataset's current_model
    # (the analysis/refined whole-crystal model). Null ⇒ fall back to the apo
    # ``structure`` artifact. This makes the built ligand appear without
    # the client needing to know which of several STRUCTURE artifacts is best.
    current_model = serializers.SerializerMethodField()
    # The model-based-map MTZ (dimple at ingest, refined servalcat MTZ after
    # refinement) — the client computes 2mFo-DFc + mFo-DFc maps from it to
    # judge the current model. Null ⇒ no map MTZ. See the map-of-record note.
    current_sf = serializers.SerializerMethodField()
    # Multi-run context (Phase E): which run produced this observation, and
    # which run-independent Finding (binding site) it belongs to. The compare
    # view groups events by `finding` and labels each by `run_group`. All
    # read-only — set at ingest by run-scoping + spatial association.
    run_id = serializers.SerializerMethodField()
    run_group = serializers.SerializerMethodField()
    finding = serializers.PrimaryKeyRelatedField(read_only=True)

    @extend_schema_field(serializers.IntegerField(allow_null=True))
    def get_run_id(self, obj):
        rd = obj.run_dataset
        return rd.run_id if rd else None

    @extend_schema_field(serializers.CharField(allow_null=True))
    def get_run_group(self, obj):
        rd = obj.run_dataset
        return rd.run.group if rd else None

    # Decision/review state lives on the run-independent Finding now; surface it
    # on the event for the unchanged single-event API. Reads resolve through the
    # Event proxy properties; writes route to event.finding (see update()).
    decision = serializers.ChoiceField(
        choices=Event.Decision.choices, required=False
    )
    confidence = serializers.CharField(
        max_length=32, required=False, allow_blank=True
    )
    comment = serializers.CharField(required=False, allow_blank=True)
    inspected_by = serializers.CharField(
        max_length=255, required=False, allow_blank=True
    )
    inspected_by_oid = serializers.CharField(read_only=True, allow_null=True)
    inspected_at = serializers.DateTimeField(read_only=True, allow_null=True)

    def update(self, instance, validated_data):
        # The writable decision fields belong to the Finding, not the Event.
        finding_fields = {
            k: validated_data.pop(k)
            for k in (
                "decision", "confidence", "comment", "inspected_by",
                "inspected_by_oid", "inspected_at",
            )
            if k in validated_data
        }
        if finding_fields:
            finding = instance.finding
            if finding is None:
                # No Finding linked (event had no usable locus at ingest) —
                # mint one on first decision so the verdict has a home.
                finding = Finding.objects.create(
                    dataset=instance.dataset,
                    centroid=(
                        instance.detection_centroid
                        or instance.xyz_centroid or []
                    ),
                )
                instance.finding = finding
                instance.save(update_fields=["finding"])
            for k, v in finding_fields.items():
                setattr(finding, k, v)
            finding.save()
        return super().update(instance, validated_data)

    @extend_schema_field(ArtifactSerializer(many=True))
    def get_artifacts(self, obj):
        # Own artifacts include the event map AND the autobuilt ligand pose
        # (LIGAND_POSE) — both event-scoped. The pose is an overlay/centre
        # target, not a model (see per-event-vs-crystal-model design note).
        own = list(obj.artifacts.all())
        shared = obj.dataset.artifacts.filter(
            kind__in=[
                Artifact.Kind.STRUCTURE,
                Artifact.Kind.LIGAND,
            ]
        )
        return ArtifactSerializer(own + list(shared), many=True).data

    @extend_schema_field(ArtifactSerializer(allow_null=True))
    def get_current_model(self, obj):
        model = obj.current_model or obj.dataset.current_model
        return ArtifactSerializer(model).data if model else None

    @extend_schema_field(ArtifactSerializer(allow_null=True))
    def get_current_sf(self, obj):
        sf = obj.dataset.current_sf
        return ArtifactSerializer(sf).data if sf else None

    class Meta:
        model = Event
        fields = [
            "id",
            "dataset",
            "dtag",
            "run_id",
            "run_group",
            "finding",
            "event_num",
            "site_num",
            "event_fraction",
            "bdc",
            "z_peak",
            "z_mean",
            "cluster_size",
            "map_resolution",
            "build_score",
            "rscc",
            "optimal_contour",
            "pose_merged",
            "xyz_centroid",
            "xyz_peak",
            # mutable inspection state — writable
            "decision",
            "confidence",
            "comment",
            "inspected_by",
            "inspected_by_oid",
            "inspected_at",
            "artifacts",
            "current_model",
            "current_sf",
            "ligand_source",
        ]
        # Analysis output is read-only; only the decision fields are writable.
        read_only_fields = [
            "dataset",
            "event_num",
            "site_num",
            "event_fraction",
            "bdc",
            "z_peak",
            "z_mean",
            "cluster_size",
            "map_resolution",
            "build_score",
            "rscc",
            "optimal_contour",
            "pose_merged",
            "xyz_centroid",
            "xyz_peak",
            # NB inspected_at / inspected_by_oid are declared explicitly above
            # with read_only=True (server-stamped from the AAD identity, never
            # client-accepted), so they are NOT listed here — DRF forbids a
            # field being both explicitly declared and in read_only_fields.
        ]


class DatasetSerializer(serializers.ModelSerializer):
    events = EventSerializer(many=True, read_only=True)
    artifacts = ArtifactSerializer(many=True, read_only=True)
    # The analysis metrics now live on RunDataset (per-run). Until the multi-run
    # compare UI lands (Phase E), the single-run API surfaces the representative
    # run's metrics so the shape is unchanged. See MULTI_RUN_DATA_MODEL.md.
    analysed_resolution = serializers.SerializerMethodField()
    high_resolution = serializers.SerializerMethodField()
    low_resolution = serializers.SerializerMethodField()
    r_free = serializers.SerializerMethodField()
    r_work = serializers.SerializerMethodField()
    map_uncertainty = serializers.SerializerMethodField()

    def _metric(self, obj, field):
        rd = obj.primary_run_dataset
        return getattr(rd, field) if rd else None

    @extend_schema_field(serializers.FloatField(allow_null=True))
    def get_analysed_resolution(self, obj):
        return self._metric(obj, "analysed_resolution")

    @extend_schema_field(serializers.FloatField(allow_null=True))
    def get_high_resolution(self, obj):
        return self._metric(obj, "high_resolution")

    @extend_schema_field(serializers.FloatField(allow_null=True))
    def get_low_resolution(self, obj):
        return self._metric(obj, "low_resolution")

    @extend_schema_field(serializers.FloatField(allow_null=True))
    def get_r_free(self, obj):
        return self._metric(obj, "r_free")

    @extend_schema_field(serializers.FloatField(allow_null=True))
    def get_r_work(self, obj):
        return self._metric(obj, "r_work")

    @extend_schema_field(serializers.FloatField(allow_null=True))
    def get_map_uncertainty(self, obj):
        return self._metric(obj, "map_uncertainty")

    class Meta:
        model = Dataset
        fields = [
            "id",
            "project",
            "dtag",
            "subtitle",
            "analysed_resolution",
            "high_resolution",
            "low_resolution",
            "r_free",
            "r_work",
            "map_uncertainty",
            "ligand_source",
            "events",
            "artifacts",
        ]


class ShellSerializer(serializers.ModelSerializer):
    class Meta:
        model = Shell
        fields = "__all__"


class ProjectSerializer(serializers.ModelSerializer):
    # Status summary computed from the related rows — the dashboard headline.
    status = serializers.SerializerMethodField()

    class Meta:
        model = Project
        fields = ["id", "name", "source_root", "ingested_at", "status"]

    @extend_schema_field(serializers.JSONField())
    def get_status(self, obj):
        from .models import Event

        from .models import Artifact, Dataset

        n_datasets = obj.datasets.count()
        events = Event.objects.filter(dataset__project=obj)
        n_events = events.count()
        # Decision lives on the linked Finding now (finding__decision); an
        # event with no Finding is unreviewed by definition.
        n_hits = events.filter(finding__decision=Event.Decision.HIT).count()
        n_no_hit = events.filter(
            finding__decision=Event.Decision.NO_HIT
        ).count()
        n_ambiguous = events.filter(
            finding__decision=Event.Decision.AMBIGUOUS
        ).count()
        n_reviewed = events.filter(finding__isnull=False).exclude(
            finding__decision=Event.Decision.UNREVIEWED
        ).count()
        n_sites = (
            events.exclude(site_num__isnull=True)
            .values("site_num")
            .distinct()
            .count()
        )
        # Curation progress beyond raw decisions: how many events have a built
        # ligand merged in, and how many crystals have a refined model.
        n_built = events.filter(pose_merged=True).count()
        n_refined = (
            Dataset.objects.filter(
                project=obj,
                current_model__origin=Artifact.Origin.REFINED,
            ).count()
        )
        # Raw distribution arrays for the dashboard's native charts: a live,
        # reader-agnostic modernisation of PanDDA1's pandda_analyse.html
        # graphs (see pandda2-summary-distributions). Binning is the client's
        # job — the contract just ships the non-null values. We OMIT
        # map_uncertainty (empty in PanDDA2 output) and the 3D event renders
        # (the Moorhen view replaces them).
        def _vals(qs, fld):
            return [
                v for v in qs.values_list(fld, flat=True) if v is not None
            ]

        # Dataset-level metrics moved to RunDataset; take one value per crystal
        # from its representative (primary) run so the histogram stays
        # one-point-per-dataset rather than one-per-run.
        primary_rds = [
            rd for rd in (d.primary_run_dataset for d in obj.datasets.all())
            if rd is not None
        ]

        def _rd_vals(fld):
            return [
                getattr(rd, fld) for rd in primary_rds
                if getattr(rd, fld) is not None
            ]

        # Event fraction = bound-state fraction = 1 − BDC. (PanDDA2 has no
        # event_fraction column; bdc is its analogue. Clamp to [0,1].)
        event_fractions = [
            max(0.0, min(1.0, 1.0 - b)) for b in _vals(events, "bdc")
        ]
        distributions = {
            "event_fraction": event_fractions,
            "event_resolution": _vals(events, "map_resolution"),
            "dataset_resolution": _rd_vals("analysed_resolution"),
            "r_free": _rd_vals("r_free"),
        }
        # Per-site rollup (events per site + hits) — backs a small site table.
        site_rows = []
        seen = events.exclude(site_num__isnull=True).values_list(
            "site_num", flat=True
        ).distinct().order_by("site_num")
        for s in seen:
            se = events.filter(site_num=s)
            site_rows.append({
                "site_num": s,
                "n_events": se.count(),
                "n_hits": se.filter(
                    finding__decision=Event.Decision.HIT
                ).count(),
                "n_no_hit": se.filter(
                    finding__decision=Event.Decision.NO_HIT
                ).count(),
                "n_ambiguous": se.filter(
                    finding__decision=Event.Decision.AMBIGUOUS
                ).count(),
            })
        return {
            "analysed": n_events > 0,
            "n_datasets": n_datasets,
            "n_events": n_events,
            "n_sites": n_sites,
            "n_hits": n_hits,
            "n_no_hit": n_no_hit,
            "n_ambiguous": n_ambiguous,
            "n_reviewed": n_reviewed,
            "n_built": n_built,
            "n_refined": n_refined,
            # Hit rate over reviewed events (None until any review happens).
            "hit_rate": (n_hits / n_reviewed) if n_reviewed else None,
            "sites": site_rows,
            "distributions": distributions,
        }


class JobSerializer(serializers.ModelSerializer):
    """A tracked compute job. Read-only over the contract; jobs are created via
    the viewset's ``submit`` action, not by POSTing a Job directly."""

    output_artifact_url = serializers.SerializerMethodField()

    class Meta:
        model = Job
        fields = [
            "id",
            "tool",
            "dataset",
            "event",
            "status",
            "spec",
            "output_artifact",
            "output_artifact_url",
            "log_relpath",
            "created_at",
            "finished_at",
        ]
        read_only_fields = fields

    @extend_schema_field(serializers.CharField(allow_null=True))
    def get_output_artifact_url(self, obj):
        if obj.output_artifact_id:
            return f"/api/v1/artifacts/{obj.output_artifact_id}/download/"
        return None


class RunRequestSerializer(serializers.Serializer):
    """Body of ``POST /runs/`` — Materia's trigger. Validation only (the view
    resolves the project and dispatches); drives the OpenAPI request schema."""

    project = serializers.CharField(
        help_text="Caller's stable project slug (stored as Project.external_id)."
    )
    group = serializers.CharField(help_text="Sub-batch label within the project.")
    share_path = serializers.CharField(
        help_text="Input dir on the shared mount (unzipped export_pandda)."
    )
    input_hash = serializers.CharField(
        required=False, allow_blank=True, default="",
        help_text="Manifest content hash; folded into the idempotency key.",
    )
    sizing_hint = serializers.DictField(
        required=False, default=dict,
        help_text="Abstract hints, e.g. {datasets, cell_volume_class}.",
    )
    params = serializers.DictField(
        required=False, default=dict,
        help_text=(
            "pandda2.analyse flag overrides, e.g. {\"memory_availability\": "
            "\"high\", \"contour_level\": \"2.0\"}. Any flag except the "
            "reserved input/output + execution-placement ones is accepted."
        ),
    )
    retry_of = serializers.IntegerField(
        required=False, allow_null=True, default=None,
        help_text="A prior run_id this explicitly retries (never deduped).",
    )


class RunSerializer(serializers.ModelSerializer):
    """A PanDDA run's status, classified failure, and log pointer (read-only;
    created via the viewset, not by POSTing a Run)."""

    run_id = serializers.SerializerMethodField()
    project = serializers.CharField(source="project.external_id",
                                    read_only=True)
    # Reinspect's numeric PK, so the run-landing page can link to the project's
    # review surface (/projects/<id>) without resolving external_id client-side.
    project_id = serializers.IntegerField(source="project.id", read_only=True)
    ui_url = serializers.SerializerMethodField()

    class Meta:
        model = Run
        fields = [
            "run_id",
            "project",
            "project_id",
            "group",
            "status",
            "share_path",
            "out_dir",
            "failure_mode",
            "failure_message",
            "log_stream_url",
            "progress",
            "parent_run_id",
            "triggered_by_oid",
            "sizing_hint",
            "params",
            "submitted_at",
            "started_at",
            "completed_at",
            "ui_url",
        ]
        read_only_fields = fields

    @extend_schema_field(serializers.CharField())
    def get_run_id(self, obj):
        return str(obj.id)

    @extend_schema_field(serializers.CharField())
    def get_ui_url(self, obj):
        base = getattr(settings, "REINSPECT_UI_BASE_URL", "") or ""
        if not base:
            request = self.context.get("request")
            if request is not None:
                base = request.build_absolute_uri("/").rstrip("/")
        return f"{base}/runs/{obj.id}"
