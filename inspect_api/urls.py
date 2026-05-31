from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register("projects", views.ProjectViewSet, basename="project")
router.register("datasets", views.DatasetViewSet, basename="dataset")
router.register("events", views.EventViewSet, basename="event")
router.register("artifacts", views.ArtifactViewSet, basename="artifact")
router.register("shells", views.ShellViewSet, basename="shell")
router.register("jobs", views.JobViewSet, basename="job")

urlpatterns = router.urls
