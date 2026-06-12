"""
Ligand comp-id normalisation — the import-boundary canonicalisation that makes
a non-LIG fragment dict (e.g. AceDRG's `DRG`) bind in the viewer and refmac.
See conventions.normalize_ligand_comp + the ligand-comp-normalisation note.
"""
import gemmi
from django.test import TestCase

from inspect_api.conventions import LIGAND_COMP, normalize_ligand_comp

_DRG_CIF = """\
data_comp_list
loop_
_chem_comp.id
_chem_comp.three_letter_code
_chem_comp.name
_chem_comp.group
DRG DRG frag non-polymer
data_comp_DRG
loop_
_chem_comp_atom.comp_id
_chem_comp_atom.atom_id
_chem_comp_atom.type_symbol
DRG C1 C
DRG O1 O
loop_
_chem_comp_bond.comp_id
_chem_comp_bond.atom_id_1
_chem_comp_bond.atom_id_2
DRG C1 O1
"""

_LIG_CIF = _DRG_CIF.replace("DRG", "LIG")


def _comp_ids(text, tag):
    doc = gemmi.cif.read_string(text)
    out = set()
    for block in doc:
        col = block.find_loop(tag)
        if col:
            out |= set(col)
    return out


class NormalizeLigandCompTests(TestCase):
    def test_renames_drg_to_lig(self):
        out = normalize_ligand_comp(_DRG_CIF)
        self.assertIn("data_comp_LIG", out)
        self.assertNotIn("DRG", out)  # no residual comp token anywhere
        # Every comp_id column + the comp_list id/tlc now read LIG.
        self.assertEqual(_comp_ids(out, "_chem_comp_bond.comp_id"), {"LIG"})
        self.assertEqual(_comp_ids(out, "_chem_comp_atom.comp_id"), {"LIG"})
        self.assertEqual(_comp_ids(out, "_chem_comp.id"), {"LIG"})
        self.assertEqual(
            _comp_ids(out, "_chem_comp.three_letter_code"), {"LIG"}
        )

    def test_atom_ids_are_not_touched(self):
        # Only .comp_id / .id / .three_letter_code columns are rewritten —
        # atom names (and anything else) are preserved verbatim.
        out = normalize_ligand_comp(_DRG_CIF)
        self.assertEqual(
            _comp_ids(out, "_chem_comp_atom.atom_id"), {"C1", "O1"}
        )

    def test_idempotent_on_lig_dict(self):
        # Already-LIG dicts return byte-identical (early-out, no reserialise).
        self.assertEqual(normalize_ligand_comp(_LIG_CIF), _LIG_CIF)

    def test_double_normalize_is_stable(self):
        once = normalize_ligand_comp(_DRG_CIF)
        self.assertEqual(normalize_ligand_comp(once), once)

    def test_unparseable_returned_unchanged(self):
        junk = "this is not a cif file"
        self.assertEqual(normalize_ligand_comp(junk), junk)

    def test_uses_lig_constant(self):
        self.assertEqual(LIGAND_COMP, "LIG")
