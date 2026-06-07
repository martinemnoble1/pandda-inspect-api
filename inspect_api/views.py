import tempfile
from pathlib import Path

from django.db import connection, models
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.utils import timezone
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response

from .buildservice import BuildError, land_built_model
from .identity import identity_from_request
from .importer import ImportError_, import_zip, ingest_path
from .jobs import get_runner
from .jobservice import JobError, refresh_job, submit_refinement
from .models import Artifact, Dataset, Event, Job, Project, Run, Shell
from .runservice import RunError, refresh_run, submit_run
from .storage import get_store
from .serializers import (
    ArtifactSerializer,
    DatasetSerializer,
    EventSerializer,
    JobSerializer,
    ProjectSerializer,
    RunRequestSerializer,
    RunSerializer,
    ShellSerializer,
)

_LOOPBACK = {"127.0.0.1", "::1", "localhost"}


def healthz(request):
    """Liveness/readiness probe for the container platform.

    Checks DB connectivity (the one external dependency that makes the app
    'ready'); 200 ``{"status": "ok"}`` when reachable, 503 otherwise. A plain
    Django view, deliberately auth-free — it is one of the paths the ccp4i2
    auth middleware exempts, so it answers even when auth is enforced. Wired at
    both ``/healthz`` (the platform probe) and ``/api/v1/health/`` (clients).
    """
    try:
        with connection.cursor() as cur:
            cur.execute("SELECT 1")
    except Exception:  # noqa: BLE001 - any DB error means not-ready
        return JsonResponse({"status": "unavailable"}, status=503)
    return JsonResponse({"status": "ok"})


def _is_local_request(request) -> bool:
    """True iff the request originates from the loopback interface.

    The path-ingest endpoint runs ingest against an arbitrary *server-side*
    directory, so it is only safe for the local desktop/CLI binding (which
    spawns the backend on 127.0.0.1). ``REMOTE_ADDR`` is the immediate peer;
    there is no reverse proxy in the Electron/dev binding, so it is the real
    client. (A hosted deployment behind a proxy would set X-Forwarded-For —
    we deliberately do NOT trust that here; loopback REMOTE_ADDR only.)
    """
    return request.META.get("REMOTE_ADDR") in _LOOPBACK


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

    @extend_schema(
        request={
            "application/json": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "path": {
                        "type": "string",
                        "description": "Absolute path to a PanDDA output "
                        "directory ON THE SERVER. Ingested in place "
                        "(source_root), not copied.",
                    },
                },
                "required": ["name", "path"],
            }
        },
        responses={
            201: OpenApiResponse(description="Ingested in place (no copy)."),
            403: OpenApiResponse(
                description="Path ingest is restricted to localhost callers."
            ),
        },
    )
    @action(detail=False, methods=["post"], url_path="ingest_path")
    def ingest_path_(self, request):
        """Ingest a PanDDA output directory **in place** by server-side path.

        This is the "ingest without copy" affordance: the desktop (Electron)
        or CLI binding hands the server a real directory path; the project's
        ``source_root`` points at it where it already lives. A browser can
        never reach this usefully (its file picker yields no path) — and it
        runs ingest against an arbitrary server path, so we **restrict it to
        localhost callers** (the Electron app and the dev machine spawn the
        backend on 127.0.0.1). Remote/hosted deployments use the zip importer.
        """
        if not _is_local_request(request):
            return Response(
                {
                    "detail": "Path ingest is only available to local "
                    "(desktop/CLI) clients. Use the zip import instead."
                },
                status=403,
            )
        name = request.data.get("name")
        path = request.data.get("path")
        if not name or not path:
            return Response(
                {"detail": "Both 'name' and 'path' are required."}, status=400
            )
        try:
            summary = ingest_path(Path(path), name)
        except ImportError_ as exc:
            return Response({"detail": str(exc)}, status=400)
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
        qs = (
            Event.objects.all()
            .select_related("dataset")
            .prefetch_related("artifacts", "dataset__artifacts")
        )
        dtag = self.request.query_params.get("dtag")
        if dtag:
            qs = qs.filter(dataset__dtag=dtag)
        project = self.request.query_params.get("project")
        if project:
            qs = qs.filter(dataset__project__name=project)
        hits_only = self.request.query_params.get("hits_only")
        if hits_only in ("1", "true", "True"):
            qs = qs.exclude(decision=Event.Decision.NO_HIT)
        return qs

    def perform_update(self, serializer):
        if "decision" in serializer.validated_data:
            extra = {"inspected_at": timezone.now()}
            # When cloud auth is on, bind the decision to the authenticated
            # curator (overriding any client-supplied inspected_by). With auth
            # off this is None, so the client-supplied value stands and the oid
            # stays null — the unchanged desktop behaviour.
            ident = identity_from_request(self.request)
            if ident is not None:
                extra["inspected_by"], extra["inspected_by_oid"] = ident
            serializer.save(**extra)
        else:
            serializer.save()

    @extend_schema(
        request={
            "application/json": {
                "type": "object",
                "properties": {
                    "pdb": {"type": "string"},
                    "merge": {"type": "boolean"},
                },
                "required": ["pdb"],
            }
        },
        responses={200: EventSerializer, 400: OpenApiResponse(
            description="Empty/invalid model, or landing failed."
        )},
    )
    @action(detail=True, methods=["post"], url_path="commit_model")
    def commit_model(self, request, pk=None):
        """Commit a client-edited crystal model as the dataset's current_model.

        The generic build primitive (DESIGN §2.2 + ligand-merge-client-side):
        the client edits the model in Moorhen/Coot — ligand merge, deleted
        waters, rotamers, alt-conf/occupancy, etc. — and POSTs the exported
        model. We persist it (origin=built, repoint Dataset.current_model).

        ``merge=true`` marks this specifically as a ligand merge for THIS event:
        sets ``pose_merged`` and stamps ``hit`` (building a ligand IS the hit
        assertion). A generic save (``merge`` absent/false) touches neither —
        deleting waters or fixing a rotamer is not a hit assertion.
        Returns the updated event (current_model now reflects the commit).
        """
        event = self.get_object()
        pdb = request.data.get("pdb")
        is_merge = bool(request.data.get("merge"))
        if not pdb:
            return Response({"detail": "'pdb' is required."}, status=400)
        try:
            land_built_model(event, pdb, pose_merged=is_merge)
        except BuildError as exc:
            return Response({"detail": str(exc)}, status=400)
        # A ligand merge IS a hit assertion — stamp it (generic saves don't).
        if is_merge and event.decision == Event.Decision.UNREVIEWED:
            event.decision = Event.Decision.HIT
            event.inspected_at = timezone.now()
            fields = ["decision", "inspected_at"]
            ident = identity_from_request(request)
            if ident is not None:
                event.inspected_by, event.inspected_by_oid = ident
                fields += ["inspected_by", "inspected_by_oid"]
            event.save(update_fields=fields)
        event.refresh_from_db()
        return Response(self.get_serializer(event).data)


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
        """Stream the artifact's bytes — from the DB if embedded (small
        dictionaries), else from the DataStore (local FS here)."""
        artifact = self.get_object()
        # Embedded artifacts (ligand CIFs) carry their bytes in the DB and live
        # outside source_root — serve directly, no path resolution/guard.
        if artifact.contents:
            resp = HttpResponse(
                artifact.contents, content_type="chemical/x-cif"
            )
            resp["Cross-Origin-Resource-Policy"] = "same-origin"
            return resp
        project = artifact.owning_project
        if project is None:
            raise Http404("Artifact has no owning project")
        # Resolve the bytes through the DataStore seam — the SINGLE place that
        # turns an artifact reference into a file, so a non-local store
        # (object storage, CCP4i2 uuids) can be slotted in without touching this
        # view (docs/MATERIA_INTEGRATION.md R6). The local store preserves the
        # deliberate guard: lexical ``..`` check BEFORE following symlinks, so
        # PanDDA2's symlinked inputs (targets outside source_root) still serve.
        store = get_store(project)
        try:
            path = store.local_path(artifact.relpath)
        except ValueError:
            raise Http404("Invalid artifact path")
        if path is None:
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


class JobViewSet(
    mixins.RetrieveModelMixin,
    mixins.ListModelMixin,
    viewsets.GenericViewSet,
):
    """
    Tracked compute jobs (refinement, …). Jobs are the single door through
    which job-produced bytes enter the artifact model (DESIGN §2/§5):

    * ``POST /jobs/submit/`` — dispatch a giant.quick_refine for a dataset.
    * ``GET /jobs/{id}/`` — poll. Refreshes status from the runner's status
      file and, on first observed success, lands the refined Artifact +
      repoints Dataset.current_model (idempotent).
    * ``GET /jobs/?dataset=… | ?project=…`` — list.
    * ``POST /jobs/{id}/cancel/`` — terminate.
    * ``GET /jobs/refine_available/`` — is the refinement env wired? (probe)
    """

    serializer_class = JobSerializer

    def get_queryset(self):
        qs = Job.objects.select_related("dataset", "output_artifact")
        dataset = self.request.query_params.get("dataset")
        if dataset:
            qs = qs.filter(dataset_id=dataset)
        project = self.request.query_params.get("project")
        if project:
            qs = qs.filter(dataset__project__name=project)
        return qs

    def retrieve(self, request, *args, **kwargs):
        # Polling endpoint: refresh from the runner (lands output on first
        # success) before serializing.
        job = refresh_job(self.get_object())
        return Response(self.get_serializer(job).data)

    @extend_schema(
        request={
            "application/json": {
                "type": "object",
                "properties": {
                    "dataset": {"type": "integer"},
                    "params": {"type": "object"},
                },
                "required": ["dataset"],
            }
        },
        responses={201: JobSerializer, 400: OpenApiResponse(
            description="Bad inputs or refinement env unavailable."
        )},
    )
    @action(detail=False, methods=["post"])
    def submit(self, request):
        """Dispatch a refinement of a dataset's current-best model."""
        dataset_id = request.data.get("dataset")
        if not dataset_id:
            return Response({"detail": "'dataset' is required."}, status=400)
        dataset = Dataset.objects.filter(pk=dataset_id).first()
        if dataset is None:
            return Response({"detail": "No such dataset."}, status=404)
        try:
            job = submit_refinement(dataset, request.data.get("params"))
        except JobError as exc:
            return Response({"detail": str(exc)}, status=400)
        return Response(self.get_serializer(job).data, status=201)

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        """Terminate a running job."""
        job = self.get_object()
        if job.runner_handle:
            get_runner().cancel(job.runner_handle)
        job = refresh_job(job)
        return Response(self.get_serializer(job).data)

    @action(detail=False, methods=["get"], url_path="refine_available")
    def refine_available(self, request):
        """Probe whether the refinement environment is wired (UI gating)."""
        return Response(get_runner().probe())


class RunViewSet(
    mixins.RetrieveModelMixin,
    mixins.ListModelMixin,
    mixins.CreateModelMixin,
    viewsets.GenericViewSet,
):
    """
    PanDDA run lifecycle — the door Materia POSTs through to trigger a run
    (docs/RUN_LIFECYCLE.md):

    * ``POST /runs/`` — trigger a run on a share-resident input group.
      Idempotent on (project, group, input_hash); an explicit ``retry_of`` is
      always a new run. Returns 201 (new) or 200 (idempotent hit).
    * ``GET /runs/{id}/`` — poll. Refreshes status from the runner and, on first
      observed success, ingests the produced pandda2_out/ tree (idempotent).
    * ``GET /runs/?project=<external_id>&group=…`` — list.
    * ``POST /runs/{id}/cancel/`` — terminate.
    """

    serializer_class = RunSerializer

    def get_queryset(self):
        qs = Run.objects.select_related("project")
        # ``project`` (external_id) is the caller-facing key; ``project_id``
        # (our PK) lets the project dashboard list its own runs without knowing
        # the external_id. No filter ⇒ the global runs list (newest-first).
        project = self.request.query_params.get("project")
        if project:
            qs = qs.filter(project__external_id=project)
        project_id = self.request.query_params.get("project_id")
        if project_id:
            qs = qs.filter(project_id=project_id)
        group = self.request.query_params.get("group")
        if group:
            qs = qs.filter(group=group)
        return qs

    def retrieve(self, request, *args, **kwargs):
        # Polling endpoint: refresh from the runner (ingests on first success)
        # before serializing.
        run = refresh_run(self.get_object())
        return Response(self.get_serializer(run).data)

    @extend_schema(
        request=RunRequestSerializer,
        responses={
            201: RunSerializer,
            200: OpenApiResponse(
                response=RunSerializer,
                description="Idempotent hit — run for this key already exists.",
            ),
            400: OpenApiResponse(description="Bad inputs / unknown retry_of."),
        },
    )
    def create(self, request, *args, **kwargs):
        body = RunRequestSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        data = body.validated_data
        # On-behalf-of provenance: stamp the authenticated human's AAD oid when
        # cloud auth is on; None for the no-auth desktop flow.
        ident = identity_from_request(request)
        oid = ident[1] if ident is not None else None
        try:
            run, created = submit_run(
                project_external_id=data["project"],
                group=data["group"],
                share_path=data["share_path"],
                input_hash=data.get("input_hash", ""),
                sizing_hint=data.get("sizing_hint") or {},
                params=data.get("params") or {},
                retry_of=data.get("retry_of"),
                triggered_by_oid=oid,
            )
        except RunError as exc:
            return Response({"detail": str(exc)}, status=400)
        status = 201 if created else 200
        return Response(
            self.get_serializer(run).data, status=status
        )

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        """Terminate a running run."""
        run = self.get_object()
        if run.runner_handle:
            get_runner().cancel(run.runner_handle)
            run.status = Run.Status.CANCELLED
            run.completed_at = timezone.now()
            run.save(update_fields=["status", "completed_at"])
        return Response(self.get_serializer(run).data)
