import { Link } from "react-router-dom";
import {
  Chip,
  Link as MuiLink,
  Paper,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Typography,
} from "@mui/material";
import { type Run, type RunStatusValue } from "../api";

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

export function RunStatusChip({ status }: { status: RunStatusValue }) {
  return (
    <Chip
      size="small"
      label={status}
      color={STATUS_COLOR[status]}
      sx={{ textTransform: "capitalize" }}
    />
  );
}

// One-line summary per run state: progress while running, the failure message
// when failed, else a dash.
function detail(run: Run): string {
  if (run.status === "running" || run.status === "provisioning") {
    return run.progress ? `Progress: ${run.progress}` : "Running…";
  }
  if (run.status === "failed") {
    return run.failure_message || "Failed";
  }
  return "—";
}

function when(iso: string): string {
  return new Date(iso).toLocaleString();
}

// Shared runs table — used by the global /runs page and the project dashboard's
// Runs section. Each row links to the run's status page (/runs/<id>).
export function RunList({ runs }: { runs: Run[] }) {
  if (runs.length === 0) {
    return (
      <Typography color="text.secondary" sx={{ py: 2 }}>
        No runs yet.
      </Typography>
    );
  }
  return (
    <TableContainer component={Paper} variant="outlined">
      <Table size="small">
        <TableHead>
          <TableRow>
            <TableCell>Run</TableCell>
            <TableCell>Group</TableCell>
            <TableCell>Status</TableCell>
            <TableCell>Detail</TableCell>
            <TableCell>Submitted</TableCell>
          </TableRow>
        </TableHead>
        <TableBody>
          {runs.map((run) => (
            <TableRow key={run.run_id} hover>
              <TableCell>
                <MuiLink component={Link} to={`/runs/${run.run_id}`}>
                  {run.run_id}
                </MuiLink>
              </TableCell>
              <TableCell>{run.group}</TableCell>
              <TableCell>
                <RunStatusChip status={run.status} />
              </TableCell>
              <TableCell sx={{ color: "text.secondary" }}>
                {detail(run)}
              </TableCell>
              <TableCell sx={{ whiteSpace: "nowrap" }}>
                {when(run.submitted_at)}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </TableContainer>
  );
}
