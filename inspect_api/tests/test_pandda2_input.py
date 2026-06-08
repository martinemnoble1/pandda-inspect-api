"""input.yaml as the authoritative PanDDA2 input manifest.

Covers the parser (typed slots, ``None``-string normalisation, resolution),
the CIFS ``mfsymlinks`` "XSym" stub detector, and the ingest wiring: ligand
source + dict CIF + resolution read from the manifest, and the in-place
materialisation that heals a stubbed ``-pandda-input.*`` from the manifest's
real path (so serving works without an mfsymlinks mount). See
docs/CLOUD_DEPLOYMENT.md (symlink note).
"""
import csv
import tempfile
from pathlib import Path

from django.core.management import call_command
from django.test import TestCase

from inspect_api.management.commands.ingest_pandda2 import Command, _InputReport
from inspect_api.models import Artifact, Project
from inspect_api.pandda2_input import (
    is_xsym_stub,
    load_input_yaml,
)

PROCESSED = "processed_datasets"


def _xsym_bytes(target: str) -> bytes:
    """A CIFS mfsymlinks symlink stub: 1067 bytes, 'XSym' magic, target text."""
    head = b"XSym\n1234\n" + b"0" * 32 + b"\n" + target.encode()
    return head.ljust(1067, b"\x00")


def _write_input_yaml(root: Path, datasets: dict) -> None:
    """datasets = {dtag: {pdb, mtz, dict_cif, ligand_pdb, smiles, resolution}}
    with None for absent slots (written as the literal 'None', as PanDDA2 does).
    """
    def slot(v):
        return v if v is not None else "None"

    lines = ["Summary:", "  Number of Datasets: %d" % len(datasets),
             "Datasets:"]
    for dtag, d in datasets.items():
        lines += [
            f"  {dtag}:",
            "    Files:",
            f"      PDB: {slot(d.get('pdb'))}",
            f"      MTZ: {slot(d.get('mtz'))}",
            "      Ligand Files:",
            "        dict:",
            f"          PDB: {slot(d.get('dict_pdb'))}",
            f"          CIF: {slot(d.get('dict_cif'))}",
            f"          SMILES: {slot(d.get('smiles'))}",
            "        ligand:",
            f"          PDB: {slot(d.get('ligand_pdb'))}",
            "          CIF: None",
            "          SMILES: None",
            "    Reflections:",
            f"      Resolution: {d.get('resolution', 1.5)}",
        ]
    (root / "input.yaml").write_text("\n".join(lines) + "\n",
                                     encoding="utf-8")


class ParserTests(TestCase):
    def _load(self, datasets):
        root = Path(tempfile.mkdtemp())
        _write_input_yaml(root, datasets)
        return load_input_yaml(root)

    def test_paths_and_resolution_parsed(self):
        m = self._load({"x1": {
            "pdb": "/d/x1/final.pdb", "mtz": "/d/x1/final.mtz",
            "dict_cif": "/d/x1/dict.cif", "ligand_pdb": "/d/x1/ligand.pdb",
            "resolution": 1.69,
        }})
        di = m["x1"]
        self.assertEqual(di.pdb, "/d/x1/final.pdb")
        self.assertEqual(di.mtz, "/d/x1/final.mtz")
        self.assertEqual(di.dict_cif, "/d/x1/dict.cif")
        self.assertEqual(di.ligand_pdb, "/d/x1/ligand.pdb")
        self.assertAlmostEqual(di.resolution, 1.69)

    def test_none_string_becomes_null(self):
        # PanDDA2 writes absent slots as the literal 'None' (a string, not a
        # YAML null) — must normalise to real None, not the text "None".
        m = self._load({"x1": {"pdb": "/d/x1/final.pdb"}})
        di = m["x1"]
        self.assertIsNone(di.dict_cif)
        self.assertIsNone(di.ligand_pdb)
        self.assertIsNone(di.smiles)

    def test_ligand_source_priority_cif_pdb_smiles_none(self):
        cif = self._load({"x": {"dict_cif": "/d.cif", "ligand_pdb": "/l.pdb",
                                "smiles": "CC"}})["x"]
        pdb = self._load({"x": {"ligand_pdb": "/l.pdb", "smiles": "CC"}})["x"]
        smi = self._load({"x": {"smiles": "CC"}})["x"]
        none = self._load({"x": {"pdb": "/p.pdb"}})["x"]
        self.assertEqual(cif.ligand_source, "cif")
        self.assertEqual(pdb.ligand_source, "pdb")
        self.assertEqual(smi.ligand_source, "smiles")
        self.assertEqual(none.ligand_source, "none")

    def test_missing_file_returns_empty(self):
        self.assertEqual(load_input_yaml(Path(tempfile.mkdtemp())), {})


class XsymStubTests(TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())

    def test_detects_1067_byte_xsym_file(self):
        p = self.dir / "stub.mtz"
        p.write_bytes(_xsym_bytes("/data/x1/final.mtz"))
        self.assertTrue(is_xsym_stub(p))

    def test_real_file_is_not_a_stub(self):
        p = self.dir / "real.mtz"
        p.write_bytes(b"MTZ " + b"\x00" * 4000)
        self.assertFalse(is_xsym_stub(p))

    def test_right_size_wrong_magic_is_not_a_stub(self):
        p = self.dir / "coincidence.bin"
        p.write_bytes(b"NOPE".ljust(1067, b"\x00"))
        self.assertFalse(is_xsym_stub(p))

    def test_absent_file_is_not_a_stub(self):
        self.assertFalse(is_xsym_stub(self.dir / "missing"))


def _write_min_tree(root: Path, dtag: str) -> None:
    """A one-event PanDDA2 tree (CSV + processed dir + event map)."""
    analyses = root / "analyses"
    analyses.mkdir(parents=True)
    proc = root / PROCESSED / dtag
    proc.mkdir(parents=True)
    with open(analyses / "pandda_analyse_events.csv", "w", newline="",
              encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=[
            "dtag", "event_idx", "site_idx", "x", "y", "z", "bdc", "1-BDC",
            "analysed_resolution",
        ])
        w.writeheader()
        w.writerow({"dtag": dtag, "event_idx": "1", "site_idx": "1",
                    "x": "1", "y": "2", "z": "3", "bdc": "0.3", "1-BDC": "0.7",
                    "analysed_resolution": ""})  # blank ⇒ manifest fallback
    (proc / f"{dtag}-event_1_1-BDC_0.7_map.native.ccp4").touch()


class IngestSelfHealTests(TestCase):
    DTAG = "xtal-0001"

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        _write_min_tree(self.root, self.DTAG)
        # The ORIGINAL data tree (the symlink targets) — reachable here.
        self.data = self.root / "data" / self.DTAG
        self.data.mkdir(parents=True)
        self.real_pdb = self.data / "final.pdb"
        self.real_mtz = self.data / "final.mtz"
        self.real_cif = self.data / "dict.cif"
        self.real_pdb.write_text(
            "ATOM      1  CA  ALA A   1       0.0   0.0   0.0\n",
            encoding="utf-8")
        self.real_mtz.write_bytes(b"MTZ " + b"\x00" * 5000)
        self.real_cif.write_text("data_LIG\n_chem_comp.id LIG\n",
                                 encoding="utf-8")
        _write_input_yaml(self.root, {self.DTAG: {
            "pdb": str(self.real_pdb), "mtz": str(self.real_mtz),
            "dict_cif": str(self.real_cif), "ligand_pdb": None,
            "resolution": 1.71,
        }})
        # The processed dir holds CIFS XSym STUBS where the symlinks should be.
        proc = self.root / PROCESSED / self.DTAG
        (proc / f"{self.DTAG}-pandda-input.pdb").write_bytes(
            _xsym_bytes(str(self.real_pdb)))
        (proc / f"{self.DTAG}-pandda-input.mtz").write_bytes(
            _xsym_bytes(str(self.real_mtz)))

    def _ingest(self):
        call_command("ingest_pandda2", "--project", "P", "--root",
                     str(self.root))
        return Project.objects.get(name="P").datasets.get(dtag=self.DTAG)

    def test_stub_inputs_are_healed_in_place(self):
        ds = self._ingest()
        proc = self.root / PROCESSED / self.DTAG
        pdb = proc / f"{self.DTAG}-pandda-input.pdb"
        mtz = proc / f"{self.DTAG}-pandda-input.mtz"
        # No longer stubs — real bytes copied from the manifest paths.
        self.assertFalse(is_xsym_stub(pdb))
        self.assertFalse(is_xsym_stub(mtz))
        self.assertEqual(pdb.read_text(encoding="utf-8"),
                         self.real_pdb.read_text(encoding="utf-8"))
        self.assertEqual(mtz.read_bytes(), self.real_mtz.read_bytes())
        # And they are catalogued + wired as the start model / SF.
        self.assertEqual(ds.current_model.relpath,
                         f"{PROCESSED}/{self.DTAG}/{self.DTAG}-pandda-input.pdb")
        self.assertEqual(ds.current_sf.relpath,
                         f"{PROCESSED}/{self.DTAG}/{self.DTAG}-pandda-input.mtz")

    def test_manifest_drives_ligand_source_cif_and_resolution(self):
        ds = self._ingest()
        self.assertEqual(ds.ligand_source, "cif")
        # CIF embedded from the manifest path (no symlink follow needed).
        lig = ds.artifacts.get(kind=Artifact.Kind.LIGAND)
        self.assertIn("LIG", lig.contents)
        # Resolution filled from input.yaml (CSV cell was blank).
        self.assertAlmostEqual(ds.analysed_resolution, 1.71)

    def test_unreachable_source_leaves_stub_and_reports(self):
        # Point the manifest at a non-existent source: cannot self-heal.
        _write_input_yaml(self.root, {self.DTAG: {
            "pdb": "/no/such/final.pdb", "mtz": "/no/such/final.mtz",
            "resolution": 1.71,
        }})
        report = _InputReport()
        rows = Command._read_csv(
            self.root / "analyses" / "pandda_analyse_events.csv")
        Command()._build_spec("P", self.root, rows, report)
        self.assertEqual(len(report.stubs), 2)
        self.assertEqual(report.healed, [])
        # The stub is left as-is (still a stub) — loud, not silently mangled.
        proc = self.root / PROCESSED / self.DTAG
        self.assertTrue(is_xsym_stub(proc / f"{self.DTAG}-pandda-input.mtz"))
