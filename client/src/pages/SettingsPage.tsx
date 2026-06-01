import { useEffect, useState } from "react";
import {
  Alert,
  Button,
  Chip,
  Container,
  Paper,
  Stack,
  TextField,
  Typography,
} from "@mui/material";
import {
  desktop,
  type DataDirInfo,
  type RefineEnvInfo,
  type RefineEnvKeys,
} from "../desktop";
import { api, type RefineAvailability } from "../api";

// Desktop-only settings. Two sections:
//   • DATA DIRECTORY — where the backend writes the SQLite DB, refinement/job
//     outputs, and zip-imported data.
//   • REFINEMENT ENVIRONMENT — the CCP4 setup script + conda env the backend
//     sources to resolve servalcat. A Finder-launched app has no login-shell
//     PATH, so we auto-detect these and let the user override here.
// The backend reads BOTH only when it is spawned, so a change needs an app
// relaunch (offered inline). Ingest-in-place projects are NOT written to the
// data dir (they keep their own source_root).
export function SettingsPage() {
  const desk = desktop();
  const [info, setInfo] = useState<DataDirInfo | null>(null);
  const [pending, setPending] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Refinement environment: the detected/effective paths, the live backend
  // probe (does servalcat actually resolve right now?), and any edits not yet
  // applied (an edit needs a relaunch to take effect).
  const [refineEnv, setRefineEnvState] = useState<RefineEnvInfo | null>(null);
  const [refineEdits, setRefineEdits] = useState<
    Partial<Record<keyof RefineEnvKeys, string>>
  >({});
  const [probe, setProbe] = useState<RefineAvailability | null>(null);
  const [refineDirty, setRefineDirty] = useState(false);

  useEffect(() => {
    if (!desk) return;
    desk.getDataDir().then(setInfo).catch((e) => setError(String(e)));
    desk.getRefineEnv().then(setRefineEnvState).catch(() => {});
    api.refineAvailable().then(setProbe).catch(() => setProbe(null));
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

  // The current value for a refine key: an unsaved edit if present, else the
  // effective (override-or-detected) value the backend would be given.
  const refineValue = (key: keyof RefineEnvKeys): string =>
    refineEdits[key] ?? refineEnv?.effective[key] ?? "";

  const editRefine = (key: keyof RefineEnvKeys, value: string) => {
    setRefineEdits((e) => ({ ...e, [key]: value }));
    setRefineDirty(true);
  };

  const pickRefineFile = async (key: keyof RefineEnvKeys, title: string) => {
    const picked = await desk.pickFile({ title, buttonLabel: "Use this file" });
    if (picked) editRefine(key, picked);
  };

  const applyRefineAndRestart = async () => {
    try {
      // Persist every key as an explicit override (so the chosen value sticks
      // even if auto-detection would differ), then relaunch to re-spawn.
      const patch: Partial<Record<keyof RefineEnvKeys, string>> = {
        CCP4_SETUP_SH: refineValue("CCP4_SETUP_SH"),
        CONDA_SH: refineValue("CONDA_SH"),
        PANDDA2_CONDA_ENV: refineValue("PANDDA2_CONDA_ENV"),
      };
      await desk.setRefineEnv(patch);
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

      <Stack spacing={3}>
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
                  <Button
                    color="inherit"
                    size="small"
                    onClick={applyAndRestart}
                  >
                    Apply & restart
                  </Button>
                }
              >
                Switch to <code>{pending}</code>? The app must restart for the
                change to take effect.
              </Alert>
            )}
          </Stack>
        </Paper>

        <Paper sx={{ p: 3 }}>
          <Stack spacing={2}>
            <Typography variant="h6">Refinement environment</Typography>
            <Typography color="text.secondary" variant="body2">
              Refinement (servalcat) lives inside a <strong>CCP4</strong> install
              and a <strong>PanDDA&nbsp;2 conda</strong> environment, which the
              backend sources before running. These are auto-detected; override
              them here if refinement is unavailable. Leave a path blank to skip
              that step.
            </Typography>

            {probe && (
              <Alert severity={probe.available ? "success" : "warning"}>
                {probe.available ? (
                  <>
                    Refinement is wired — <code>{probe.tool}</code> resolves to{" "}
                    <code>{probe.resolved}</code>.
                  </>
                ) : (
                  <>
                    Refinement unavailable: {probe.reason || "tool not found"}.
                  </>
                )}
              </Alert>
            )}

            <TextField
              label="CCP4 setup script (ccp4.setup-sh)"
              value={refineValue("CCP4_SETUP_SH")}
              onChange={(e) => editRefine("CCP4_SETUP_SH", e.target.value)}
              fullWidth
              size="small"
              helperText={
                refineEnv && !refineEnv.overridden.CCP4_SETUP_SH
                  ? refineEnv.detected.CCP4_SETUP_SH
                    ? "Auto-detected"
                    : "Not detected — set it manually"
                  : "Custom"
              }
            />
            <Button
              size="small"
              variant="outlined"
              sx={{ alignSelf: "flex-start" }}
              onClick={() =>
                pickRefineFile("CCP4_SETUP_SH", "Select the CCP4 setup script")
              }
            >
              Browse…
            </Button>

            <TextField
              label="conda.sh"
              value={refineValue("CONDA_SH")}
              onChange={(e) => editRefine("CONDA_SH", e.target.value)}
              fullWidth
              size="small"
              helperText={
                refineEnv && !refineEnv.overridden.CONDA_SH
                  ? refineEnv.detected.CONDA_SH
                    ? "Auto-detected"
                    : "Not detected — set it manually"
                  : "Custom"
              }
            />
            <Button
              size="small"
              variant="outlined"
              sx={{ alignSelf: "flex-start" }}
              onClick={() => pickRefineFile("CONDA_SH", "Select conda.sh")}
            >
              Browse…
            </Button>

            <TextField
              label="PanDDA 2 conda env name"
              value={refineValue("PANDDA2_CONDA_ENV")}
              onChange={(e) => editRefine("PANDDA2_CONDA_ENV", e.target.value)}
              fullWidth
              size="small"
            />

            {refineDirty && (
              <Alert
                severity="warning"
                action={
                  <Button
                    color="inherit"
                    size="small"
                    onClick={applyRefineAndRestart}
                  >
                    Apply & restart
                  </Button>
                }
              >
                The app must restart for the new refinement environment to take
                effect.
              </Alert>
            )}

            {!refineDirty && refineEnv && (
              <Chip
                size="small"
                sx={{ alignSelf: "flex-start" }}
                label={`env: ${refineValue("PANDDA2_CONDA_ENV") || "(none)"}`}
              />
            )}
          </Stack>
        </Paper>

        {error && <Alert severity="error">{error}</Alert>}
      </Stack>
    </Container>
  );
}
