import { useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  Accordion,
  AccordionDetails,
  AccordionSummary,
  Alert,
  Box,
  Button,
  Container,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import ExpandMoreIcon from "@mui/icons-material/ExpandMore";
import { api } from "../api";

// pandda2.analyse option defaults — must mirror the backend
// (jobs.build_pandda2_argv). The regexes encode the export_pandda ↔ pandda2
// file-naming contract; editable here, but changing them only makes sense if
// the input tree uses different names.
const OPTION_DEFAULTS = {
  pdb_regex: "final.pdb",
  mtz_regex: "final.mtz",
  ligand_cif_regex: "dict.cif",
  ligand_pdb_regex: "ligand.pdb",
  dataset_range: "0-999999999",
  local_cpus: "1",
};

type Options = typeof OPTION_DEFAULTS;

const OPTION_LABELS: Record<keyof Options, string> = {
  pdb_regex: "PDB regex",
  mtz_regex: "MTZ regex",
  ligand_cif_regex: "Ligand CIF regex",
  ligand_pdb_regex: "Ligand PDB regex",
  dataset_range: "Dataset range",
  local_cpus: "Local CPUs",
};

// "Run PanDDA" form. Triggers POST /runs/ and lands on the run's status page.
// The input dir is a path the BACKEND can see (the mounted share in cloud, a
// local path on desktop) — a browser can't pick a server directory.
export function NewRun() {
  const navigate = useNavigate();
  const [project, setProject] = useState("");
  const [group, setGroup] = useState("");
  const [sharePath, setSharePath] = useState("");
  const [opts, setOpts] = useState<Options>(OPTION_DEFAULTS);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const canSubmit = project && group && sharePath && !busy;

  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      const run = await api.triggerRun({
        project,
        group,
        share_path: sharePath,
        params: opts,
      });
      navigate(`/runs/${run.run_id}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setBusy(false);
    }
  };

  return (
    <Container maxWidth="sm">
      <Box sx={{ mt: 4 }}>
        <Typography variant="h4" gutterBottom>
          Run PanDDA
        </Typography>
        {error && (
          <Alert severity="error" sx={{ mb: 2 }}>
            {error}
          </Alert>
        )}
        <Stack spacing={2}>
          <TextField
            label="Project"
            value={project}
            onChange={(e) => setProject(e.target.value)}
            helperText="Project slug (created if new)."
            required
          />
          <TextField
            label="Group"
            value={group}
            onChange={(e) => setGroup(e.target.value)}
            helperText="Sub-batch label within the project."
            required
          />
          <TextField
            label="Input directory"
            value={sharePath}
            onChange={(e) => setSharePath(e.target.value)}
            helperText="Server-side path to the export_pandda inputs (its
              datasets/ dir is analysed)."
            required
          />

          <Accordion variant="outlined" disableGutters>
            <AccordionSummary expandIcon={<ExpandMoreIcon />}>
              <Typography>Advanced options</Typography>
            </AccordionSummary>
            <AccordionDetails>
              <Stack spacing={2}>
                {(Object.keys(OPTION_DEFAULTS) as (keyof Options)[]).map(
                  (k) => (
                    <TextField
                      key={k}
                      size="small"
                      label={OPTION_LABELS[k]}
                      value={opts[k]}
                      onChange={(e) =>
                        setOpts({ ...opts, [k]: e.target.value })
                      }
                    />
                  )
                )}
              </Stack>
            </AccordionDetails>
          </Accordion>

          <Button
            variant="contained"
            size="large"
            disabled={!canSubmit}
            onClick={submit}
          >
            {busy ? "Submitting…" : "Run PanDDA"}
          </Button>
        </Stack>
      </Box>
    </Container>
  );
}
