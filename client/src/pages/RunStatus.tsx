import { useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  Alert,
  Box,
  Button,
  CircularProgress,
  Container,
  LinearProgress,
  Stack,
  Typography,
} from "@mui/material";
import { api, type Run, type RunStatusValue } from "../api";
import { RunStatusChip } from "../components/RunList";

const TERMINAL: RunStatusValue[] = ["succeeded", "failed", "cancelled"];
const POLL_MS = 3000;

// Parse a progress string like "dataset 9/120" into a bar fraction. Returns
// null when there's no N/M to show (→ indeterminate bar).
function parseCount(progress: string | null) {
  const m = progress?.match(/(\d+)\s*\/\s*(\d+)/);
  if (!m) return null;
  const done = Number(m[1]);
  const total = Number(m[2]);
  if (!total) return null;
  return { done, total, pct: Math.min(100, Math.round((done / total) * 100)) };
}

function fmtDuration(ms: number): string {
  const s = Math.max(0, Math.floor(ms / 1000));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  if (h) return `${h}h ${String(m).padStart(2, "0")}m`;
  if (m) return `${m}m ${String(sec).padStart(2, "0")}s`;
  return `${sec}s`;
}

// One timing line appropriate to the run's state: queued-for / running-for
// while active, total duration once terminal.
function timingLabel(run: Run, now: number): string {
  const sub = Date.parse(run.submitted_at);
  const start = run.started_at ? Date.parse(run.started_at) : null;
  const done = run.completed_at ? Date.parse(run.completed_at) : null;
  if (TERMINAL.includes(run.status)) {
    if (start && done) return `Ran in ${fmtDuration(done - start)}`;
    if (done) return `Finished in ${fmtDuration(done - sub)}`;
    return "";
  }
  if (start) return `Running for ${fmtDuration(now - start)}`;
  return `Queued for ${fmtDuration(now - sub)}`;
}

// Phase text for the non-terminal states (when there's no dataset count yet).
function phaseText(status: RunStatusValue): string {
  if (status === "provisioning") return "Provisioning a compute node…";
  if (status === "queued") return "Queued…";
  return "Starting analysis…";
}

// Landing page for a triggered PanDDA run: the API's ui_url points here
// (/runs/<id>). Polls the run until it reaches a terminal state, surfacing
// live progress + timing and — on success — a link to the review surface.
export function RunStatus() {
  const { runId } = useParams<{ runId: string }>();
  const [run, setRun] = useState<Run | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [now, setNow] = useState(Date.now());
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

  // Tick the clock once a second so "Running for …" advances live.
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, []);

  const active = run ? !TERMINAL.includes(run.status) : false;
  const count = run ? parseCount(run.progress) : null;

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
              <RunStatusChip status={run.status} />
              <Typography color="text.secondary">
                {run.project} / {run.group}
              </Typography>
              <Box sx={{ flexGrow: 1 }} />
              <Typography variant="caption" color="text.secondary">
                {timingLabel(run, now)}
              </Typography>
            </Stack>

            {active && (
              <Box>
                <LinearProgress
                  variant={count ? "determinate" : "indeterminate"}
                  value={count?.pct}
                  sx={{ mb: 1, height: 8, borderRadius: 1 }}
                />
                <Typography variant="body2" color="text.secondary">
                  {count
                    ? `Dataset ${count.done} of ${count.total} (${count.pct}%)`
                    : phaseText(run.status)}
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
