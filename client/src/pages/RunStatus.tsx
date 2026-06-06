import { useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  Container,
  LinearProgress,
  Stack,
  Typography,
} from "@mui/material";
import { api, type Run, type RunStatusValue } from "../api";

const TERMINAL: RunStatusValue[] = ["succeeded", "failed", "cancelled"];
const POLL_MS = 3000;

const STATUS_COLOR: Record<
  RunStatusValue,
  "default" | "info" | "success" | "error" | "warning"
> = {
  queued: "default",
  provisioning: "info",
  running: "info",
  succeeded: "success",
  failed: "error",
  cancelled: "warning",
};

// Landing page for a triggered PanDDA run: the API's ui_url points here
// (/runs/<id>). Polls the run until it reaches a terminal state, surfacing
// progress and — on success — a link to the project's review surface.
export function RunStatus() {
  const { runId } = useParams<{ runId: string }>();
  const [run, setRun] = useState<Run | null>(null);
  const [error, setError] = useState<string | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  useEffect(() => {
    if (!runId) return;
    let cancelled = false;

    const poll = async () => {
      try {
        const r = await api.getRun(runId);
        if (cancelled) return;
        setRun(r);
        setError(null);
        if (!TERMINAL.includes(r.status)) {
          timer.current = setTimeout(poll, POLL_MS);
        }
      } catch (e) {
        if (cancelled) return;
        setError(e instanceof Error ? e.message : String(e));
        timer.current = setTimeout(poll, POLL_MS);
      }
    };
    poll();

    return () => {
      cancelled = true;
      if (timer.current) clearTimeout(timer.current);
    };
  }, [runId]);

  return (
    <Container maxWidth="sm">
      <Box sx={{ mt: 6 }}>
        <Typography variant="h4" gutterBottom>
          PanDDA run
        </Typography>

        {!run && !error && (
          <Stack direction="row" spacing={2} alignItems="center" sx={{ mt: 4 }}>
            <CircularProgress size={24} />
            <Typography color="text.secondary">Loading run…</Typography>
          </Stack>
        )}

        {error && !run && (
          <Alert severity="error" sx={{ mt: 2 }}>
            Could not load run {runId}: {error}
          </Alert>
        )}

        {run && (
          <Stack spacing={3} sx={{ mt: 2 }}>
            <Stack direction="row" spacing={1.5} alignItems="center">
              <Chip
                label={run.status}
                color={STATUS_COLOR[run.status]}
                sx={{ textTransform: "capitalize" }}
              />
              <Typography color="text.secondary">
                {run.project} / {run.group}
              </Typography>
            </Stack>

            {!TERMINAL.includes(run.status) && (
              <Box>
                <LinearProgress sx={{ mb: 1 }} />
                <Typography variant="body2" color="text.secondary">
                  {run.progress
                    ? `Progress: ${run.progress}`
                    : "Waiting for the analysis to report progress…"}
                </Typography>
              </Box>
            )}

            {run.status === "succeeded" && (
              <Stack spacing={2}>
                <Alert severity="success">
                  Run complete — events have been ingested.
                </Alert>
                <Button
                  variant="contained"
                  component={Link}
                  to={`/projects/${run.project_id}`}
                >
                  Review events
                </Button>
              </Stack>
            )}

            {run.status === "failed" && (
              <Alert severity="error">
                <Typography fontWeight={600}>
                  {run.failure_message || "Run failed"}
                </Typography>
                {run.failure_mode && (
                  <Typography variant="body2" sx={{ mt: 0.5 }}>
                    Failure mode: <code>{run.failure_mode}</code>
                  </Typography>
                )}
              </Alert>
            )}

            {run.status === "cancelled" && (
              <Alert severity="warning">This run was cancelled.</Alert>
            )}
          </Stack>
        )}
      </Box>
    </Container>
  );
}
