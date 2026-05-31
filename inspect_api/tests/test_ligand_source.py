"""
Tests for ligand-spec provenance classification (DESIGN §6.2).

The PanDDA2 reader resolves the original data/<dtag>/ dir via the
-pandda-input.pdb symlink, then classifies the best-available ligand slot
(cif > pdb > smiles > none), mirroring PanDDA2's LigandFiles model. BAZ2B is
all-cif, so these hermetic cases prove the pdb/smiles/none branches + priority
that real data can't exercise.
"""
import tempfile
from pathlib import Path

from django.test import TestCase

from inspect_api.management.commands.ingest_pandda2 import Command


def _layout(*ligand_files):
    """Build processed/<dtag>/ with -pandda-input.pdb symlinked to a sibling
    data/<dtag>/ holding the given ligand files; return (processed_ddir, dtag).
    """
    dtag = "ds1"
    root = Path(tempfile.mkdtemp())
    data_dir = root / "data" / dtag
    data_dir.mkdir(parents=True)
    real_pdb = data_dir / f"{dtag}.dimple.pdb"
    real_pdb.write_text("REAL", encoding="utf-8")
    for name in ligand_files:
        (data_dir / name).write_text("x", encoding="utf-8")

    proc = root / "processed_datasets" / dtag
    proc.mkdir(parents=True)
    # PanDDA2 symlinks the input pdb from data/<dtag>/ into the processed dir.
    (proc / f"{dtag}-pandda-input.pdb").symlink_to(real_pdb)
    return proc, dtag


class LigandSourceClassifyTests(TestCase):
    def _classify(self, *files):
        ddir, dtag = _layout(*files)
        return Command._classify_ligand_source(ddir, dtag)

    def test_cif_detected(self):
        self.assertEqual(self._classify("ligand.cif"), "cif")

    def test_pdb_only(self):
        self.assertEqual(self._classify("ligand.pdb"), "pdb")

    def test_smiles_only(self):
        self.assertEqual(self._classify("ligand.smiles"), "smiles")

    def test_none_when_no_ligand_files(self):
        self.assertEqual(self._classify(), "none")

    def test_priority_cif_beats_pdb_and_smiles(self):
        self.assertEqual(
            self._classify("ligand.cif", "ligand.pdb", "ligand.smiles"),
            "cif",
        )

    def test_priority_pdb_beats_smiles(self):
        self.assertEqual(
            self._classify("ligand.pdb", "ligand.smiles"), "pdb"
        )

    def test_input_pdb_does_not_count_as_ligand_pdb(self):
        # The dataset's own coords are present (the dimple.pdb), but that is
        # NOT a ligand.pdb — must not be misclassified as 'pdb'.
        self.assertEqual(self._classify(), "none")
