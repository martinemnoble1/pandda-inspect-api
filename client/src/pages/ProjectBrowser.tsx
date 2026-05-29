import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {
  Box,
  Card,
  CardActionArea,
  CardContent,
  Chip,
  CircularProgress,
  Grid,
  Stack,
  Typography,
} from "@mui/material";
import { api, type Project } from "../api";

export function ProjectBrowser() {
  const [projects, setProjects] = useState<Project[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .listProjects()
      .then((d) => setProjects(d.results))
      .catch((e) => setError(String(e)));
  }, []);

  if (error) return <Typography color="error">{error}</Typography>;
  if (!projects) return <CircularProgress />;
  if (projects.length === 0)
    return (
      <Typography color="text.secondary">
        No projects yet — import one to get started.
      </Typography>
    );

  return (
    <Box>
      <Typography variant="h4" gutterBottom>
        Projects
      </Typography>
      <Grid container spacing={2}>
        {projects.map((p) => (
          <Grid key={p.id} size={{ xs: 12, sm: 6, md: 4 }}>
            <Card>
              <CardActionArea
                component={Link}
                to={`/projects/${p.id}`}
              >
                <CardContent>
                  <Typography variant="h6">{p.name}</Typography>
                  <Stack
                    direction="row"
                    spacing={1}
                    sx={{ mt: 1, flexWrap: "wrap", gap: 1 }}
                  >
                    <Chip
                      size="small"
                      color={p.status.analysed ? "success" : "default"}
                      label={p.status.analysed ? "Analysed" : "Not analysed"}
                    />
                    <Chip
                      size="small"
                      label={`${p.status.n_datasets} datasets`}
                    />
                    <Chip size="small" label={`${p.status.n_events} events`} />
                    <Chip size="small" label={`${p.status.n_sites} sites`} />
                  </Stack>
                </CardContent>
              </CardActionArea>
            </Card>
          </Grid>
        ))}
      </Grid>
    </Box>
  );
}
