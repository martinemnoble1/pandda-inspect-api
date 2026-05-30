import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  Box,
  Button,
  Card,
  CardContent,
  CircularProgress,
  Grid,
  Paper,
  Tab,
  Tabs,
  Typography,
} from "@mui/material";
import ScienceIcon from "@mui/icons-material/Science";
import { api, type Artifact, type Project } from "../api";

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
  const [tab, setTab] = useState(0);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.getProject(id).then(setProject).catch((e) => setError(String(e)));
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

      <Typography variant="h6" gutterBottom>
        Reports
      </Typography>
      {reports.length === 0 ? (
        <Typography color="text.secondary">No HTML reports found.</Typography>
      ) : (
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
      )}
    </Box>
  );
}
