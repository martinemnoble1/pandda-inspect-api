from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from .models import Artifact, Dataset, Event, Job, Project, Shell


class ArtifactSerializer(serializers.ModelSerializer):
    # A client-resolvable URL to stream the bytes through the API.
    download_url = serializers.SerializerMethodField()

    class Meta:
        model = Artifact
        fields = [
            "id", "kind", "relpath", "project", "dataset", "event",
            "download_url",
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

    class Meta:
        model = Event
        fields = [
            "id",
            "dataset",
            "dtag",
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
            "xyz_centroid",
            "xyz_peak",
            # mutable inspection state — writable
            "decision",
            "confidence",
            "comment",
            "inspected_by",
            "inspected_at",
            "artifacts",
            "current_model",
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
            "xyz_centroid",
            "xyz_peak",
            "inspected_at",
        ]


class DatasetSerializer(serializers.ModelSerializer):
    events = EventSerializer(many=True, read_only=True)
    artifacts = ArtifactSerializer(many=True, read_only=True)

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

        n_datasets = obj.datasets.count()
        events = Event.objects.filter(dataset__project=obj)
        n_events = events.count()
        n_hits = events.filter(decision=Event.Decision.HIT).count()
        n_reviewed = events.exclude(
            decision=Event.Decision.UNREVIEWED
        ).count()
        n_sites = (
            events.exclude(site_num__isnull=True)
            .values("site_num")
            .distinct()
            .count()
        )
        return {
            "analysed": n_events > 0,
            "n_datasets": n_datasets,
            "n_events": n_events,
            "n_sites": n_sites,
            "n_hits": n_hits,
            "n_reviewed": n_reviewed,
            # Hit rate over reviewed events (None until any review happens).
            "hit_rate": (n_hits / n_reviewed) if n_reviewed else None,
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
