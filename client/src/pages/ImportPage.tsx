import { useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  Alert,
  Box,
  Button,
  Container,
  LinearProgress,
  Paper,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import { api } from "../api";

export function ImportPage() {
  const [name, setName] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<any>(null);
  const navigate = useNavigate();

  const submit = async () => {
    if (!name || !file) return;
    setBusy(true);
    setError(null);
    try {
      const r = await api.importZip(name, file);
      setResult(r);
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
        Upload a zip of either a <strong>PanDDA output directory</strong> (the
        flavour containing <code>pandda/results.json</code>) or a{" "}
        <strong>crystals directory with a manifest</strong>. The server detects
        the flavour, lands it, and ingests it into the store.
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
          <Button variant="outlined" component="label" disabled={busy}>
            {file ? file.name : "Choose .zip file"}
            <input
              type="file"
              accept=".zip"
              hidden
              onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            />
          </Button>
          {busy && (
            <Box>
              <LinearProgress />
              <Typography variant="caption" color="text.secondary">
                Uploading and ingesting…
              </Typography>
            </Box>
          )}
          {error && <Alert severity="error">{error}</Alert>}
          {result && (
            <Alert severity="success">
              Imported <strong>{result.project}</strong> ({result.flavour},{" "}
              {result.n_datasets} datasets).
              <Button
                size="small"
                sx={{ ml: 2 }}
                onClick={() => navigate("/projects")}
              >
                View projects
              </Button>
            </Alert>
          )}
          <Button
            variant="contained"
            onClick={submit}
            disabled={busy || !name || !file}
          >
            Import
          </Button>
        </Stack>
      </Paper>
    </Container>
  );
}
