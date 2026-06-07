import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  Box,
  Button,
  Card,
  CardContent,
  Chip,
  CircularProgress,
  Grid,
  Paper,
  Stack,
  Tab,
  Tabs,
  Typography,
} from "@mui/material";
import ScienceIcon from "@mui/icons-material/Science";
import { api, type Artifact, type Project, type Run } from "../api";
import { SummaryCharts } from "../components/SummaryCharts";
import { RunList } from "../components/RunList";

function StatCard({ label, value }: { label: string; value: string | number }) {
  return (
    <Card>
      <CardContent>
        <Typography variant="h4">{value}</Typography>
        <Typography color="text.secondary">{label}</Typography>
      </CardContent>
    </Card>
  );
}

export function ProjectDashboard() {
  const { projectId } = useParams();
  const id = Number(projectId);
  const [project, setProject] = useState<Project | null>(null);
  const [reports, setReports] = useState<Artifact[]>([]);
  const [runs, setRuns] = useState<Run[]>([]);
  const [tab, setTab] = useState(0);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.getProject(id).then(setProject).catch((e) => setError(String(e)));
    // This project's PanDDA runs (cloud-triggered); empty for desktop/CLI
    // projects, in which case the section below simply isn't shown.
    api
      .listRuns(id)
      .then((p) => setRuns(p.results))
      .catch(() => setRuns([]));
    // A catalogued report may not be on disk (e.g. pandda_inspect.html before
    // inspection). Probe each and keep only those that actually serve, so the
    // iframe never shows a broken tab.
    api
      .projectReports(id)
      .then(async (all) => {
        const checks = await Promise.all(
          all.map(async (r) => {
            try {
              const resp = await fetch(api.artifactUrl(r), { method: "HEAD" });
              return resp.ok ? r : null;
            } catch {
              return null;
            }
          })
        );
        setReports(checks.filter((r): r is Artifact => r !== null));
      })
      .catch(() => setReports([]));
  }, [id]);

  if (error) return <Typography color="error">{error}</Typography>;
  if (!project) return <CircularProgress />;

  const s = project.status;
  const reportName = (a: Artifact) =>
    a.relpath.split("/").pop()?.replace(/\.html$/, "") ?? a.relpath;

  return (
    <Box>
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          mb: 2,
        }}
      >
        <Typography variant="h4">{project.name}</Typography>
        <Button
          variant="contained"
          size="large"
          startIcon={<ScienceIcon />}
          component={Link}
          to={`/projects/${id}/inspect`}
        >
          Open in Moorhen
        </Button>
      </Box>

      <Grid container spacing={2} sx={{ mb: 3 }}>
        <Grid size={{ xs: 6, md: 3 }}>
          <StatCard label="Datasets" value={s.n_datasets} />
        </Grid>
        <Grid size={{ xs: 6, md: 3 }}>
          <StatCard label="Events" value={s.n_events} />
        </Grid>
        <Grid size={{ xs: 6, md: 3 }}>
          <StatCard label="Sites" value={s.n_sites} />
        </Grid>
        <Grid size={{ xs: 6, md: 3 }}>
          <StatCard
            label="Hit rate"
            value={
              s.hit_rate === null
                ? "—"
                : `${Math.round(s.hit_rate * 100)}%`
            }
          />
        </Grid>
      </Grid>

      {/* DB-backed project summary — a live modernisation of PanDDA1's
          pandda_analyse.html (PanDDA2 emits no HTML reports). Curation progress
          + per-site distribution, reflecting the current inspection state. */}
      <Typography variant="h6" gutterBottom>
        Summary
      </Typography>
      <Paper variant="outlined" sx={{ p: 2, mb: 3 }}>
        <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
          <Chip label={`${s.n_reviewed}/${s.n_events} events reviewed`} />
          <Chip color="success" label={`${s.n_hits} hits`} />
          <Chip color="error" variant="outlined" label={`${s.n_no_hit} no-hit`} />
          {s.n_ambiguous > 0 && (
            <Chip variant="outlined" label={`${s.n_ambiguous} ambiguous`} />
          )}
          <Chip
            color="info"
            variant="outlined"
            label={`${s.n_built} ligands built`}
          />
          <Chip
            color="info"
            variant="outlined"
            label={`${s.n_refined} crystals refined`}
          />
        </Stack>
        <SummaryCharts distributions={s.distributions} sites={s.sites} />
      </Paper>

      {/* PanDDA runs for this project (cloud-triggered). Mirrors the Reports
          pattern: shown only when there are runs, so desktop/CLI projects
          aren't cluttered with an empty section. */}
      {runs.length > 0 && (
        <Box sx={{ mb: 3 }}>
          <Typography variant="h6" gutterBottom>
            Runs
          </Typography>
          <RunList runs={runs} />
        </Box>
      )}

      {/* Legacy external HTML reports (PanDDA1 only — PanDDA2 emits none, in
          which case the live Summary above is the report and this section
          simply isn't shown, rather than announcing an expected absence). */}
      {reports.length > 0 && (
        <>
          <Typography variant="h6" gutterBottom>
            Reports
          </Typography>
          <Paper variant="outlined">
            <Tabs
              value={tab}
              onChange={(_, v) => setTab(v)}
              variant="scrollable"
              scrollButtons="auto"
            >
              {reports.map((r) => (
                <Tab key={r.id} label={reportName(r)} />
              ))}
            </Tabs>
            <Box sx={{ height: "70vh" }}>
              {reports[tab] && (
                <iframe
                  title={reportName(reports[tab])}
                  src={api.artifactUrl(reports[tab])}
                  style={{ width: "100%", height: "100%", border: "none" }}
                />
              )}
            </Box>
          </Paper>
        </>
      )}
    </Box>
  );
}
