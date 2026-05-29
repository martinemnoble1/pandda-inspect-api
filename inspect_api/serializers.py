from rest_framework import serializers

from .models import Artifact, Dataset, Event, Project, Shell


class ArtifactSerializer(serializers.ModelSerializer):
    # A client-resolvable URL to stream the bytes through the API.
    download_url = serializers.SerializerMethodField()

    class Meta:
        model = Artifact
        fields = ["id", "kind", "relpath", "event", "download_url"]

    def get_download_url(self, obj):
        return f"/api/v1/artifacts/{obj.id}/download/"


class EventSerializer(serializers.ModelSerializer):
    dtag = serializers.CharField(source="dataset.dtag", read_only=True)
    artifacts = ArtifactSerializer(many=True, read_only=True)

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
            "xyz_centroid",
            "xyz_peak",
            # mutable inspection state — writable
            "decision",
            "confidence",
            "comment",
            "inspected_by",
            "inspected_at",
            "artifacts",
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
            "events",
            "artifacts",
        ]


class ShellSerializer(serializers.ModelSerializer):
    class Meta:
        model = Shell
        fields = "__all__"


class ProjectSerializer(serializers.ModelSerializer):
    n_datasets = serializers.IntegerField(
        source="datasets.count", read_only=True
    )

    class Meta:
        model = Project
        fields = ["id", "name", "source_root", "ingested_at", "n_datasets"]
