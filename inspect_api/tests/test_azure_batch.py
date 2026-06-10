"""
AzureBatchRunner — the step-2 cloud runner behind PANDDA_JOB_RUNNER=azure_batch.

The pure helpers (state mapping, handle, log pointer) and the runner's
lifecycle wiring are tested against a fake Batch client with NO Azure SDK
involved, so they run in CI (which doesn't install azure-batch). The one test
that constructs a real Batch model (submit) is guarded on the SDK being
importable. NB: live submission against a real Batch account is the integration
test Materia owns — the mocked tests cover logic, not the wire.
"""
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from django.test import TestCase

from inspect_api import azure_batch as ab
from inspect_api import runservice
from inspect_api.jobs import JobSpec
from inspect_api.models import Dataset, Event, Project, Run


def _azure_batch_available() -> bool:
    try:
        import azure.batch  # noqa: F401
    except ImportError:
        return False
    return True


def _task(state, exit_code=None, failure_message=None):
    failure = (
        SimpleNamespace(message=failure_message)
        if failure_message is not None else None
    )
    info = SimpleNamespace(exit_code=exit_code, failure_info=failure)
    return SimpleNamespace(state=state, execution_info=info)


def _container_pool(image="materia/pandda:latest", registries=None,
                    vm_size="Standard_E16ds_v4"):
    """A pool object shaped like a container-enabled Batch pool."""
    return SimpleNamespace(
        vm_size=vm_size,
        virtual_machine_configuration=SimpleNamespace(
            container_configuration=SimpleNamespace(
                container_image_names=[image],
                container_registries=registries or [],
            )
        )
    )


class FakeBatchClient:
    def __init__(self, task=None, stdout=b"", pool=None):
        self._task = task
        self._stdout = stdout
        self._pool = pool
        self.created_jobs = []
        self.created_tasks = []
        self.terminated = []

    def pool_exists(self, pool_id):
        return True

    def get_pool(self, pool_id):
        return self._pool

    def get_task(self, job_id, task_id):
        return self._task

    def download_task_file(self, job_id, task_id, path):
        return [self._stdout]

    def create_job(self, job):
        self.created_jobs.append(job)

    def create_task(self, job_id, task):
        self.created_tasks.append((job_id, task))

    def terminate_task(self, job_id, task_id):
        self.terminated.append((job_id, task_id))


class PureHelpersTest(TestCase):
    def test_handle_roundtrip(self):
        self.assertEqual(ab.make_handle("j", "run-5"), "j/run-5")
        self.assertEqual(ab.split_handle("j/run-5"), ("j", "run-5"))

    def test_log_pointer(self):
        self.assertEqual(
            ab.log_pointer("https://a.b.batch.azure.com/", "j", "run-5"),
            "https://a.b.batch.azure.com/jobs/j/tasks/run-5/files/stdout.txt",
        )

    def test_state_running(self):
        for s in ("active", "preparing", "running"):
            self.assertEqual(ab.normalise_task_state(_task(s))["state"],
                             "running")

    def test_state_succeeded(self):
        st = ab.normalise_task_state(_task("completed", exit_code=0))
        self.assertEqual(st["state"], "succeeded")

    def test_state_failed_by_exit_code(self):
        st = ab.normalise_task_state(_task("completed", exit_code=1))
        self.assertEqual(st["state"], "failed")
        self.assertIn("code 1", st["failure_message"])

    def test_state_failed_by_failure_info(self):
        st = ab.normalise_task_state(
            _task("completed", exit_code=0, failure_message="scheduling boom")
        )
        self.assertEqual(st["state"], "failed")
        self.assertEqual(st["failure_message"], "scheduling boom")

    def test_state_unknown_is_running(self):
        self.assertEqual(
            ab.normalise_task_state(_task(None))["state"], "running"
        )


class RunnerWithFakeClientTest(TestCase):
    def _runner(self, **kw):
        return ab.AzureBatchRunner(
            pool_id="poolX", job_id="pandda-runs",
            endpoint="https://acct.uksouth.batch.azure.com", **kw,
        )

    def test_status_maps_and_surfaces_log(self):
        client = FakeBatchClient(
            task=_task("running"),
            stdout=b"warming\nPANDDA_PROGRESS: dataset 3/120\n",
        )
        st = self._runner(client=client).status("pandda-runs/run-7")
        self.assertEqual(st["state"], "running")
        self.assertIn("dataset 3/120", st["log"])
        self.assertTrue(st["log_url"].endswith(
            "/jobs/pandda-runs/tasks/run-7/files/stdout.txt"
        ))

    def test_cancel_terminates_task(self):
        client = FakeBatchClient()
        self._runner(client=client).cancel("pandda-runs/run-7")
        self.assertEqual(client.terminated, [("pandda-runs", "run-7")])

    def test_probe_ok(self):
        self.assertTrue(self._runner(client=FakeBatchClient())
                        .probe()["available"])

    def test_command_line_is_the_invocation_contract(self):
        cmd = self._runner(client=FakeBatchClient())._command_line(
            JobSpec(tool="pandda2.analyse",
                    inputs={"data_dirs": "/share/in/datasets"},
                    params={"out_dir": "/share/out", "local_cpus": 8})
        )
        self.assertTrue(cmd.startswith("pandda2.analyse"))
        self.assertIn("--data_dirs /share/in/datasets", cmd)
        self.assertIn("--out_dir /share/out", cmd)
        self.assertIn("--local_cpus 8", cmd)

    @unittest.skipUnless(_azure_batch_available(),
                         "azure-batch not installed")
    def test_submit_ensures_job_and_creates_task(self):
        client = FakeBatchClient()
        handle = self._runner(client=client).submit(
            JobSpec(tool="pandda2.analyse",
                    inputs={"data_dirs": "/in/datasets"},
                    params={"out_dir": "/out"}),
            Path("/tmp/runs/42"),
        )
        self.assertEqual(handle, "pandda-runs/run-42")
        self.assertEqual(len(client.created_jobs), 1)        # _ensure_job
        self.assertEqual(len(client.created_tasks), 1)
        self.assertEqual(client.created_tasks[0][0], "pandda-runs")
        # Non-container pool (get_pool → None): no container_settings.
        _, task = client.created_tasks[0]
        self.assertIsNone(getattr(task, "container_settings", None))

    @unittest.skipUnless(_azure_batch_available(),
                         "azure-batch not installed")
    def test_submit_attaches_container_settings_for_container_pool(self):
        client = FakeBatchClient(pool=_container_pool())
        self._runner(client=client).submit(
            JobSpec(tool="pandda2.analyse",
                    inputs={"data_dirs": "/in/datasets"},
                    params={"out_dir": "/out"}),
            Path("/tmp/runs/9"),
        )
        _, task = client.created_tasks[0]
        cs = task.container_settings
        self.assertEqual(cs.image_name, "materia/pandda:latest")
        self.assertIn("/mnt/projects", cs.container_run_options)

    @unittest.skipUnless(_azure_batch_available(),
                         "azure-batch not installed")
    def test_container_image_env_override(self):
        client = FakeBatchClient(pool=_container_pool())
        with mock.patch.dict(
            "os.environ",
            {"AZURE_BATCH_CONTAINER_IMAGE": "myacr.io/pandda:pinned",
             "AZURE_BATCH_CONTAINER_RUN_OPTIONS": "-v /data:/data"},
        ):
            self._runner(client=client).submit(
                JobSpec(tool="pandda2.analyse",
                        inputs={"data_dirs": "/in/datasets"},
                        params={"out_dir": "/out"}),
                Path("/tmp/runs/10"),
            )
        cs = client.created_tasks[0][1].container_settings
        self.assertEqual(cs.image_name, "myacr.io/pandda:pinned")
        self.assertEqual(cs.container_run_options, "-v /data:/data")


class RefreshRunConsumesBatchStatusTest(TestCase):
    """refresh_run is runner-agnostic: it consumes the log/log_url/state a
    remote runner surfaces, without reading a local job.log."""

    def setUp(self):
        self.project = Project.objects.create(name="P", source_root="/tmp/x")
        self.dataset = Dataset.objects.create(project=self.project, dtag="d1")
        self.run = Run.objects.create(
            project=self.project, group="g", share_path="/share/in",
            out_dir="/share/out", idempotency_key="k1",
            status=Run.Status.RUNNING, runner_handle="pandda-runs/run-1",
        )

    def _patch(self, status_dict):
        runner = SimpleNamespace(status=lambda h: status_dict)
        return mock.patch.object(runservice, "get_runner",
                                 return_value=runner)

    def test_running_sets_progress_and_log_url_from_status(self):
        with self._patch({
            "state": "running",
            "log": "PANDDA_PROGRESS: dataset 9/120\n",
            "log_url": "https://acct/jobs/pandda-runs/tasks/run-1/files/stdout.txt",
        }):
            runservice.refresh_run(self.run)
        self.run.refresh_from_db()
        self.assertEqual(self.run.progress, "dataset 9/120")
        self.assertTrue(self.run.log_stream_url.endswith("stdout.txt"))

    def test_failed_classifies_from_surfaced_log(self):
        with self._patch({
            "state": "failed",
            "log": "...\nMemoryError: out of memory\n",
            "log_url": "https://acct/.../stdout.txt",
        }):
            runservice.refresh_run(self.run)
        self.run.refresh_from_db()
        self.assertEqual(self.run.status, "failed")
        self.assertEqual(self.run.failure_mode, "oom")


class LocalCpusTest(TestCase):
    """--local_cpus sizing for the Batch node (memory-bound)."""

    def test_vcpus_from_vm_size(self):
        self.assertEqual(ab._vcpus_from_vm_size("Standard_E16ds_v4"), 16)
        # Constrained-core size → usable count.
        self.assertEqual(ab._vcpus_from_vm_size("Standard_E32-8ds_v4"), 8)
        self.assertEqual(ab._vcpus_from_vm_size("Standard_D8as_v5"), 8)
        self.assertIsNone(ab._vcpus_from_vm_size("weird"))
        self.assertIsNone(ab._vcpus_from_vm_size(None))

    def test_pick_cpus_mapping(self):
        # Large/huge leave RAM headroom proportional to the node: vcpus-2 and
        # vcpus//2 on a 16-core box.
        self.assertEqual(
            ab._pick_cpus({"cell_volume_class": "large"},
                          "Standard_E16ds_v4"), 14)
        self.assertEqual(
            ab._pick_cpus({"cell_volume_class": "huge"},
                          "Standard_E16ds_v4"), 8)
        # Headroom never drops below one worker on a tiny node.
        self.assertEqual(
            ab._pick_cpus({"cell_volume_class": "large"},
                          "Standard_E2ds_v4"), 1)
        # Headroom class with an unparseable node → omit (PanDDA2 default).
        self.assertIsNone(ab._pick_cpus({"cell_volume_class": "large"}))
        # Unmapped class falls through to the VM's vCPU count.
        self.assertEqual(
            ab._pick_cpus({"cell_volume_class": "small"},
                          "Standard_E16ds_v4"), 16)
        self.assertIsNone(ab._pick_cpus({}))  # no vm_size → None

    def _cmd(self, sizing=None, params=None, pool=None):
        runner = ab.AzureBatchRunner(
            client=FakeBatchClient(pool=pool), pool_id="p", endpoint="e",
        )
        return runner._command_line(JobSpec(
            tool="pandda2.analyse",
            inputs={"data_dirs": "/in/datasets"},
            params={"out_dir": "/out", **(params or {})},
            sizing_hint=sizing or {},
        ))

    def test_default_uses_pool_vcpus(self):
        cmd = self._cmd({}, pool=_container_pool(vm_size="Standard_E16ds_v4"))
        self.assertIn("--local_cpus 16", cmd)  # not PanDDA2's default of 6

    def test_large_leaves_headroom_on_big_node(self):
        cmd = self._cmd({"cell_volume_class": "large"},
                        pool=_container_pool(vm_size="Standard_E16ds_v4"))
        self.assertIn("--local_cpus 14", cmd)  # vcpus-2, not all 16

    def test_no_pool_info_omits_flag(self):
        self.assertNotIn("--local_cpus", self._cmd({}))

    def test_explicit_param_beats_sizing(self):
        cmd = self._cmd({"cell_volume_class": "large"}, {"local_cpus": "4"},
                        pool=_container_pool())
        self.assertIn("--local_cpus 4", cmd)

    def test_env_overrides_everything(self):
        with mock.patch.dict("os.environ",
                             {"AZURE_BATCH_LOCAL_CPUS": "12"}):
            cmd = self._cmd({"cell_volume_class": "large"},
                            {"local_cpus": "4"},
                            pool=_container_pool(vm_size="Standard_E16ds_v4"))
        self.assertIn("--local_cpus 12", cmd)
