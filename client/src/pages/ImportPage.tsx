import { useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  Alert,
  Box,
  Button,
  Chip,
  Container,
  Divider,
  LinearProgress,
  Paper,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import { api } from "../api";
import { desktop } from "../desktop";

export function ImportPage() {
  const [name, setName] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [folder, setFolder] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const navigate = useNavigate();
  const desk = desktop();

  // Desktop only: pick a PanDDA output directory to ingest IN PLACE (no copy).
  const pickFolder = async () => {
    if (!desk) return;
    const picked = await desk.pickDirectory({
      title: "Select a PanDDA output directory",
      buttonLabel: "Use folder",
    });
    if (picked) {
      setFolder(picked);
      setFile(null); // folder + zip are mutually exclusive inputs
    }
  };

  const submit = async () => {
    if (!name || (!file && !folder)) return;
    setBusy(true);
    setError(null);
    try {
      const r = folder
        ? await api.ingestPath(name, folder)
        : await api.importZip(name, file!);
      // Land straight on the new project's summary. Fall back to the project
      // list only if the importer didn't return an id (older backend).
      if (r?.id != null) {
        navigate(`/projects/${r.id}`);
      } else {
        navigate("/projects");
      }
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Container maxWidth="sm">
      <Typography variant="h4" gutterBottom>
        Import a dataset
      </Typography>
      <Typography color="text.secondary" paragraph>
        Point at a <strong>PanDDA output directory</strong> — the server
        detects the flavour from the files inside it:{" "}
        <strong>PanDDA&nbsp;2</strong> (containing{" "}
        <code>analyses/pandda_analyse_events.csv</code>) or{" "}
        <strong>PanDDA&nbsp;1</strong> (containing{" "}
        <code>pandda/results.json</code>). A{" "}
        <strong>crystals directory with a manifest</strong> works too. The
        server lands it and ingests it into the store.
        {desk && (
          <>
            {" "}
            In the desktop app you can <strong>ingest a folder in place</strong>{" "}
            — pointed at where it already lives, with no copy. Otherwise upload a
            zip of the directory.
          </>
        )}
      </Typography>

      <Paper sx={{ p: 3 }}>
        <Stack spacing={2}>
          <TextField
            label="Project name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            fullWidth
            disabled={busy}
          />

          {/* Desktop: ingest-in-place by folder (no copy). */}
          {desk && (
            <>
              <Button
                variant="outlined"
                onClick={pickFolder}
                disabled={busy}
              >
                {folder ? "Change folder…" : "Browse folder (ingest in place)…"}
              </Button>
              {folder && (
                <Chip
                  label={folder}
                  onDelete={busy ? undefined : () => setFolder(null)}
                  sx={{
                    maxWidth: "100%",
                    "& .MuiChip-label": {
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                    },
                  }}
                />
              )}
              <Divider>or upload a zip</Divider>
            </>
          )}

          <Button
            variant="outlined"
            component="label"
            disabled={busy || !!folder}
          >
            {file ? file.name : "Choose .zip file"}
            <input
              type="file"
              accept=".zip"
              hidden
              onChange={(e) => {
                setFile(e.target.files?.[0] ?? null);
                setFolder(null);
              }}
            />
          </Button>

          {busy && (
            <Box>
              <LinearProgress />
              <Typography variant="caption" color="text.secondary">
                {folder ? "Ingesting in place…" : "Uploading and ingesting…"}
              </Typography>
            </Box>
          )}
          {error && <Alert severity="error">{error}</Alert>}
          <Button
            variant="contained"
            onClick={submit}
            disabled={busy || !name || (!file && !folder)}
          >
            Import
          </Button>
        </Stack>
      </Paper>
    </Container>
  );
}
