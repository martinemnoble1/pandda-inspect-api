import { Box, Button, Container, Stack, Typography } from "@mui/material";
import { Link } from "react-router-dom";

export function Landing() {
  return (
    <Container maxWidth="md">
      <Box sx={{ mt: 6, textAlign: "center" }}>
        <Typography variant="h3" gutterBottom>
          PanDDA Inspect
        </Typography>
        <Typography variant="h6" color="text.secondary" paragraph>
          Browse PanDDA analyses, triage events, and inspect electron density
          in Moorhen — all over a clean REST API.
        </Typography>
        <Typography color="text.secondary" paragraph sx={{ mt: 2 }}>
          A contract-first reference client. The filesystem is an import
          boundary; everything you see here is served from a transactional
          store through a versioned API.
        </Typography>
        <Stack
          direction="row"
          spacing={2}
          justifyContent="center"
          sx={{ mt: 4 }}
        >
          <Button
            variant="contained"
            size="large"
            component={Link}
            to="/projects"
          >
            Browse projects
          </Button>
          <Button
            variant="outlined"
            size="large"
            component={Link}
            to="/import"
          >
            Import a dataset
          </Button>
        </Stack>
      </Box>
    </Container>
  );
}
