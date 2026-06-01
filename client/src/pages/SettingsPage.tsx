import { useEffect, useState } from "react";
import {
  Alert,
  Button,
  Container,
  Paper,
  Stack,
  Typography,
} from "@mui/material";
import { desktop, type DataDirInfo } from "../desktop";

// Desktop-only settings. The one setting so far is the DATA DIRECTORY — where
// the backend writes the SQLite DB, refinement/job outputs, and zip-imported
// data. The backend reads it only when it is spawned, so a change needs an
// app relaunch (offered inline). Ingest-in-place projects are NOT written here
// (they keep their own source_root).
export function SettingsPage() {
  const desk = desktop();
  const [info, setInfo] = useState<DataDirInfo | null>(null);
  const [pending, setPending] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (desk) desk.getDataDir().then(setInfo).catch((e) => setError(String(e)));
  }, [desk]);

  if (!desk) {
    return (
      <Container maxWidth="sm">
        <Typography variant="h4" gutterBottom>
          Settings
        </Typography>
        <Alert severity="info">
          Settings are only available in the desktop app.
        </Alert>
      </Container>
    );
  }

  const choose = async () => {
    setError(null);
    const picked = await desk.pickDirectory({
      title: "Choose where Reinspect stores data",
      buttonLabel: "Use this folder",
    });
    if (picked) setPending(picked);
  };

  const applyAndRestart = async () => {
    if (!pending) return;
    try {
      await desk.setDataDir(pending);
      await desk.relaunch();
    } catch (e) {
      setError(String(e));
    }
  };

  return (
    <Container maxWidth="sm">
      <Typography variant="h4" gutterBottom>
        Settings
      </Typography>

      <Paper sx={{ p: 3 }}>
        <Stack spacing={2}>
          <Typography variant="h6">Data folder</Typography>
          <Typography color="text.secondary" variant="body2">
            Where the database, refinement outputs, and imported (copied) data
            are written. Folders <strong>ingested in place</strong> are read
            from where they live and are not affected.
          </Typography>

          <Alert severity="info">
            Current: <code>{info?.path ?? "…"}</code>
            {info?.isDefault ? " (default)" : ""}
          </Alert>

          <Button variant="outlined" onClick={choose}>
            Choose a different folder…
          </Button>

          {pending && (
            <Alert
              severity="warning"
              action={
                <Button color="inherit" size="small" onClick={applyAndRestart}>
                  Apply & restart
                </Button>
              }
            >
              Switch to <code>{pending}</code>? The app must restart for the
              change to take effect.
            </Alert>
          )}

          {error && <Alert severity="error">{error}</Alert>}
        </Stack>
      </Paper>
    </Container>
  );
}
