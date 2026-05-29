from pathlib import Path

from django.conf import settings
from django.http import FileResponse, Http404
from django.utils import timezone
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import mixins, viewsets
from rest_framework.decorators import action

from .models import Artifact, Dataset, Event, Project, Shell
from .serializers import (
    ArtifactSerializer,
    DatasetSerializer,
    EventSerializer,
    ProjectSerializer,
    ShellSerializer,
)


class ProjectViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Project.objects.all()
    serializer_class = ProjectSerializer


class DatasetViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = DatasetSerializer

    def get_queryset(self):
        qs = Dataset.objects.all().prefetch_related("events", "artifacts")
        project = self.request.query_params.get("project")
        if project:
            qs = qs.filter(project__name=project)
        return qs


class EventViewSet(
    mixins.RetrieveModelMixin,
    mixins.ListModelMixin,
    mixins.UpdateModelMixin,  # PATCH to record a decision
    viewsets.GenericViewSet,
):
    """
    Events are read-only except for the inspection decision fields. A PATCH
    that sets ``decision`` stamps ``inspected_at`` — the kind of atomic,
    constraint-backed mutation the filesystem model cannot provide.
    """

    serializer_class = EventSerializer

    def get_queryset(self):
        qs = Event.objects.all().select_related("dataset").prefetch_related(
            "artifacts"
        )
        dtag = self.request.query_params.get("dtag")
        if dtag:
            qs = qs.filter(dataset__dtag=dtag)
        hits_only = self.request.query_params.get("hits_only")
        if hits_only in ("1", "true", "True"):
            qs = qs.exclude(decision=Event.Decision.NO_HIT)
        return qs

    def perform_update(self, serializer):
        if "decision" in serializer.validated_data:
            serializer.save(inspected_at=timezone.now())
        else:
            serializer.save()


class ArtifactViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Artifact.objects.select_related("dataset", "dataset__project")
    serializer_class = ArtifactSerializer

    @extend_schema(
        responses={
            200: OpenApiResponse(description="Raw artifact bytes streamed."),
            404: OpenApiResponse(description="Artifact file not found on disk."),
        }
    )
    @action(detail=True, methods=["get"])
    def download(self, request, pk=None):
        """Stream the artifact's bytes from the DataStore (local FS here)."""
        artifact = self.get_object()
        root = Path(settings.PANDDA_DATA_ROOT) / artifact.dataset.project.name
        path = (root / artifact.relpath).resolve()
        # Guard against path traversal escaping the project root.
        if not str(path).startswith(str(root.resolve())):
            raise Http404("Invalid artifact path")
        if not path.is_file():
            raise Http404(f"Artifact not on disk: {artifact.relpath}")
        return FileResponse(open(path, "rb"))


class ShellViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Shell.objects.all()
    serializer_class = ShellSerializer
