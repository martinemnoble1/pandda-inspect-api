/**
 * Native dashboard distribution charts — a live, reader-agnostic modernisation
 * of PanDDA1's pandda_analyse.html graphs (which were matplotlib PNGs with
 * external `../` refs, neither iframe-servable nor portable). We render the
 * same distributions Nick Pearce considered diagnostically important straight
 * from relational data, so they work identically for PanDDA1 and PanDDA2.
 */
import { useMemo } from "react";
import { Box, Grid, Typography } from "@mui/material";
import {
  BarElement,
  CategoryScale,
  Chart as ChartJS,
  LinearScale,
  Title,
  Tooltip,
} from "chart.js";
import { Bar } from "react-chartjs-2";
import type { Distributions, SiteSummary } from "../api";

ChartJS.register(CategoryScale, LinearScale, BarElement, Title, Tooltip);

// Bin raw values into a fixed number of equal-width buckets, labelled by the
// bucket midpoint. Returns empty arrays when there's nothing to plot so the
// caller can skip the chart entirely.
function histogram(values: number[], bins: number) {
  if (values.length === 0) return { labels: [], counts: [] };
  const lo = Math.min(...values);
  const hi = Math.max(...values);
  if (hi === lo) {
    return { labels: [lo.toFixed(2)], counts: [values.length] };
  }
  const width = (hi - lo) / bins;
  const counts = new Array(bins).fill(0);
  for (const v of values) {
    const idx = Math.min(bins - 1, Math.floor((v - lo) / width));
    counts[idx] += 1;
  }
  const labels = counts.map((_, i) => (lo + width * (i + 0.5)).toFixed(2));
  return { labels, counts };
}

const CHART_OPTS = {
  responsive: true,
  maintainAspectRatio: false,
  plugins: { legend: { display: false } },
  scales: { y: { beginAtZero: true, ticks: { precision: 0 } } },
};

function Histogram({
  title,
  values,
  bins = 20,
  color,
}: {
  title: string;
  values: number[];
  bins?: number;
  color: string;
}) {
  const { labels, counts } = useMemo(
    () => histogram(values, bins),
    [values, bins]
  );
  if (counts.length === 0) return null;
  return (
    <Box>
      <Typography variant="subtitle2" gutterBottom>
        {title} <Typography component="span" color="text.secondary">
          (n={values.length})
        </Typography>
      </Typography>
      <Box sx={{ height: 200 }}>
        <Bar
          data={{
            labels,
            datasets: [{ data: counts, backgroundColor: color }],
          }}
          options={CHART_OPTS}
        />
      </Box>
    </Box>
  );
}

export function SummaryCharts({
  distributions,
  sites,
}: {
  distributions: Distributions;
  sites: SiteSummary[];
}) {
  // Events-per-site, STACKED by decision (replaces PanDDA1's
  // analyse_events_site_N pies). One bar per site whose total height is the
  // event count, segmented into hit / no-hit / ambiguous / unreviewed — so
  // curation progress AND the decision mix read off the same axis, and no-hit /
  // ambiguous are visible (they weren't on the old Events+Hits overlay). Colours
  // mirror the event-chip decision semantics. Unreviewed = events minus decided.
  const siteData = useMemo(
    () => ({
      labels: sites.map((s) => `Site ${s.site_num}`),
      datasets: [
        {
          label: "Hits",
          data: sites.map((s) => s.n_hits),
          backgroundColor: "#66bb6a",
          stack: "decisions",
        },
        {
          label: "No-hit",
          data: sites.map((s) => s.n_no_hit),
          backgroundColor: "#ef5350",
          stack: "decisions",
        },
        {
          label: "Ambiguous",
          data: sites.map((s) => s.n_ambiguous),
          backgroundColor: "#ffb74d",
          stack: "decisions",
        },
        {
          label: "Unreviewed",
          data: sites.map((s) =>
            Math.max(0, s.n_events - s.n_hits - s.n_no_hit - s.n_ambiguous)
          ),
          backgroundColor: "#e0e0e0",
          stack: "decisions",
        },
      ],
    }),
    [sites]
  );

  return (
    <Grid container spacing={3} sx={{ mt: 0.5 }}>
      <Grid size={{ xs: 12, md: 6 }}>
        <Histogram
          title="Event fraction (1 − BDC)"
          values={distributions.event_fraction}
          color="#7e57c2"
        />
      </Grid>
      <Grid size={{ xs: 12, md: 6 }}>
        <Histogram
          title="Event resolution (Å)"
          values={distributions.event_resolution}
          color="#26a69a"
        />
      </Grid>
      <Grid size={{ xs: 12, md: 6 }}>
        <Histogram
          title="Dataset resolution (Å)"
          values={distributions.dataset_resolution}
          color="#42a5f5"
        />
      </Grid>
      <Grid size={{ xs: 12, md: 6 }}>
        <Histogram
          title="R-free"
          values={distributions.r_free}
          color="#ef5350"
        />
      </Grid>
      {sites.length > 0 && (
        <Grid size={{ xs: 12 }}>
          <Typography variant="subtitle2" gutterBottom>
            Events per site
          </Typography>
          <Box sx={{ height: 220 }}>
            <Bar
              data={siteData}
              options={{
                ...CHART_OPTS,
                plugins: { legend: { display: true } },
                scales: {
                  x: { stacked: true },
                  y: {
                    stacked: true,
                    beginAtZero: true,
                    ticks: { precision: 0 },
                  },
                },
              }}
            />
          </Box>
        </Grid>
      )}
    </Grid>
  );
}
