"""
AzureBlobStore against Azurite (the local Azure Blob emulator).

The DataStore seam already abstracts "artifact reference -> bytes"; this proves
the Azure binding satisfies it. It is the cloud half of R6 with no Azure-side
decision required: it runs entirely against Azurite. Skipped unless both the
SDK is importable and ``AZURE_STORAGE_CONNECTION_STRING`` /
``AZURE_STORAGE_CONTAINER`` are set, so the default hermetic ``manage.py test``
run skips it cleanly.

To run it::

    docker run -d -p 10000:10000 mcr.microsoft.com/azure-storage/azurite \\
        azurite-blob --blobHost 0.0.0.0 --skipApiVersionCheck
    # --skipApiVersionCheck: recent azure-storage-blob SDKs negotiate an API
    # version newer than a given Azurite image knows; the flag lets Azurite
    # accept it instead of 400-ing with InvalidHeaderValue.
    export AZURE_STORAGE_CONNECTION_STRING="DefaultEndpointsProtocol=http;\\
AccountName=devstoreaccount1;AccountKey=Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT\\
50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw==;\\
BlobEndpoint=http://127.0.0.1:10000/devstoreaccount1;"
    export AZURE_STORAGE_CONTAINER=reinspect-test
    python manage.py test inspect_api.tests.test_storage_azure
"""
import os
import unittest

from django.test import TestCase

from inspect_api import storage
from inspect_api.models import Project

CONN = os.environ.get("AZURE_STORAGE_CONNECTION_STRING")
CONTAINER = os.environ.get("AZURE_STORAGE_CONTAINER")


def _azurite_available() -> bool:
    try:
        import azure.storage.blob  # noqa: F401
    except ImportError:
        return False
    return bool(CONN and CONTAINER)


@unittest.skipUnless(
    _azurite_available(), "azure-storage-blob + Azurite env required"
)
class AzureBlobStoreTest(TestCase):
    def setUp(self):
        from azure.storage.blob import BlobServiceClient

        service = BlobServiceClient.from_connection_string(CONN)
        self.container = service.get_container_client(CONTAINER)
        try:
            self.container.create_container()
        except Exception:  # noqa: BLE001 - already exists is fine
            pass
        self.project = Project.objects.create(
            name="proj-az", source_root=""
        )
        # Upload under the project's key prefix; relpaths are resolved beneath.
        self.container.upload_blob(
            "proj-az/sub/foo.txt", b"hello-blob", overwrite=True
        )

    def tearDown(self):
        try:
            self.container.delete_blob("proj-az/sub/foo.txt")
        except Exception:  # noqa: BLE001
            pass

    def test_round_trip(self):
        store = storage.AzureBlobStore(self.project)
        self.assertTrue(store.exists("sub/foo.txt"))
        self.assertFalse(store.exists("sub/missing.txt"))
        self.assertEqual(store.open("sub/foo.txt").read(), b"hello-blob")

    def test_local_path_materialises(self):
        store = storage.AzureBlobStore(self.project)
        path = store.local_path("sub/foo.txt")
        self.assertIsNotNone(path)
        self.assertEqual(path.read_bytes(), b"hello-blob")
        self.assertIsNone(store.local_path("sub/missing.txt"))

    def test_traversal_guard(self):
        store = storage.AzureBlobStore(self.project)
        with self.assertRaises(ValueError):
            store.exists("../escape.txt")

    def test_get_store_selector_returns_azure(self):
        prev = os.environ.get("PANDDA_DATA_STORE")
        os.environ["PANDDA_DATA_STORE"] = "azure"
        try:
            store = storage.get_store(self.project)
            self.assertIsInstance(store, storage.AzureBlobStore)
        finally:
            if prev is None:
                os.environ.pop("PANDDA_DATA_STORE", None)
            else:
                os.environ["PANDDA_DATA_STORE"] = prev
