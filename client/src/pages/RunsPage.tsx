import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {
  Alert,
  Box,
  Button,
  CircularProgress,
  Container,
  Stack,
  Typography,
} from "@mui/material";
import AddIcon from "@mui/icons-material/Add";
import { api, type Run } from "../api";
import { RunList } from "../components/RunList";

const REFRESH_MS = 10000;

// Global runs view (newest-first) — the re-discovery / overview surface for
// runs triggered elsewhere (e.g. the embedding app). Light auto-refresh keeps
// in-flight statuses current. NB shows all runs (no per-user scoping yet).
export function RunsPage() {
  const [runs, setRuns] = useState<Run[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const page = await api.listRuns();
        if (!cancelled) {
          setRuns(page.results);
          setError(null);
        }
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      }
    };
    load();
    const id = setInterval(load, REFRESH_MS);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  return (
    <Container maxWidth="lg">
      <Box sx={{ mt: 4 }}>
        <Box
          sx={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            mb: 2,
          }}
        >
          <Typography variant="h4">Runs</Typography>
          <Button
            variant="contained"
            startIcon={<AddIcon />}
            component={Link}
            to="/runs/new"
          >
            New run
          </Button>
        </Box>
        {error && (
          <Alert severity="error" sx={{ mb: 2 }}>
            Could not load runs: {error}
          </Alert>
        )}
        {!runs && !error && (
          <Stack direction="row" spacing={2} alignItems="center" sx={{ mt: 3 }}>
            <CircularProgress size={24} />
            <Typography color="text.secondary">Loading runs…</Typography>
          </Stack>
        )}
        {runs && <RunList runs={runs} />}
      </Box>
    </Container>
  );
}
