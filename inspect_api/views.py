import tempfile
from pathlib import Path

from django.conf import settings
from django.db import models
from django.http import FileResponse, Http404
from django.utils import timezone
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response

from .importer import ImportError_, import_zip
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

    @action(detail=True, methods=["get"])
    def reports(self, request, pk=None):
        """List this project's HTML reports (for the dashboard iframe panel)."""
        project = self.get_object()
        qs = project.artifacts.filter(kind=Artifact.Kind.REPORT_HTML)
        return Response(ArtifactSerializer(qs, many=True).data)

    @extend_schema(
        request={
            "multipart/form-data": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "file": {"type": "string", "format": "binary"},
                },
                "required": ["name", "file"],
            }
        },
        responses={201: OpenApiResponse(description="Imported + ingested.")},
    )
    @action(
        detail=False,
        methods=["post"],
        url_path="import",
        parser_classes=[MultiPartParser, FormParser],
    )
    def import_(self, request):
        """
        Import a zip (PanDDA output, or crystals+manifest), landing it under
        the data root and ingesting it. This is the write side of the import
        boundary; afterwards the API serves SQL.
        """
        name = request.data.get("name")
        upload = request.FILES.get("file")
        if not name or not upload:
            return Response(
                {"detail": "Both 'name' and 'file' are required."},
                status=400,
            )
        with tempfile.NamedTemporaryFile(
            suffix=".zip", delete=False
        ) as tmp:
            for chunk in upload.chunks():
                tmp.write(chunk)
            tmp_path = Path(tmp.name)
        try:
            summary = import_zip(tmp_path, name)
        except ImportError_ as exc:
            return Response({"detail": str(exc)}, status=400)
        finally:
            tmp_path.unlink(missing_ok=True)
        return Response(summary, status=201)


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
    serializer_class = ArtifactSerializer

    def get_queryset(self):
        qs = Artifact.objects.select_related(
            "project", "dataset", "dataset__project"
        )
        project = self.request.query_params.get("project")
        if project:
            qs = qs.filter(
                models.Q(project__name=project)
                | models.Q(dataset__project__name=project)
            )
        kind = self.request.query_params.get("kind")
        if kind:
            qs = qs.filter(kind=kind)
        dtag = self.request.query_params.get("dtag")
        if dtag:
            qs = qs.filter(dataset__dtag=dtag)
        return qs

    @extend_schema(
        responses={
            200: OpenApiResponse(description="Raw artifact bytes streamed."),
            404: OpenApiResponse(description="Artifact not on disk."),
        }
    )
    @action(detail=True, methods=["get"])
    def download(self, request, pk=None):
        """Stream the artifact's bytes from the DataStore (local FS here)."""
        artifact = self.get_object()
        project = artifact.owning_project
        if project is None:
            raise Http404("Artifact has no owning project")
        root = Path(settings.PANDDA_DATA_ROOT) / project.name
        path = (root / artifact.relpath).resolve()
        # Guard against path traversal escaping the project root.
        if not str(path).startswith(str(root.resolve())):
            raise Http404("Invalid artifact path")
        if not path.is_file():
            raise Http404(f"Artifact not on disk: {artifact.relpath}")
        resp = FileResponse(open(path, "rb"))
        # The client runs under COEP=require-corp (for Moorhen's WASM), so any
        # subresource it fetches — report HTML in an iframe, maps/coords into
        # Coot — must opt in with CORP. Same-origin here (proxied), so:
        resp["Cross-Origin-Resource-Policy"] = "same-origin"
        return resp


class ShellViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Shell.objects.all()
    serializer_class = ShellSerializer
