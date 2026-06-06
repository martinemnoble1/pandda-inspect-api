/**
 * The only thing the client knows about the backend: the REST contract.
 * No filesystem paths, no panddaPrefix — just typed calls to /api/v1.
 */
// Public path prefix the app is served under (Vite `base`, e.g. "/reinspect/"
// when path-mounted; "/" by default → empty prefix, unchanged for desktop).
// Every backend URL — our own calls AND server-emitted absolute ones like
// download_url — is prefixed with this so they route through the mount.
const PREFIX = import.meta.env.BASE_URL.replace(/\/$/, "");
const BASE = `${PREFIX}/api/v1`;

export interface SiteSummary {
  site_num: number;
  n_events: number;
  n_hits: number;
}
// Raw (unbinned) distribution values backing the dashboard's native charts —
// a live modernisation of PanDDA1's pandda_analyse.html graphs. The client
// bins them. event_fraction = 1 − BDC (bound-state fraction). map_uncertainty
// is deliberately absent (empty in PanDDA2 output).
export interface Distributions {
  event_fraction: number[];
  event_resolution: number[];
  dataset_resolution: number[];
  r_free: number[];
}
export interface ProjectStatus {
  analysed: boolean;
  n_datasets: number;
  n_events: number;
  n_sites: number;
  n_hits: number;
  n_no_hit: number;
  n_ambiguous: number;
  n_reviewed: number;
  n_built: number;
  n_refined: number;
  hit_rate: number | null;
  sites: SiteSummary[];
  distributions: Distributions;
}
export interface Project {
  id: number;
  name: string;
  source_root: string;
  ingested_at: string;
  status: ProjectStatus;
}
export interface MapColumn {
  F: string;
  PHI: string;
  isDifference: boolean;
}
export interface Artifact {
  id: number;
  kind: string;
  relpath: string;
  project: number | null;
  dataset: number | null;
  event: number | null;
  download_url: string;
  // For MTZ artifacts: explicit map-coefficient columns (one per map to
  // compute). The client loads with these labels — no Coot heuristics.
  map_columns: MapColumn[];
}
export type JobStatus = "queued" | "running" | "succeeded" | "failed";
export interface Job {
  id: number;
  tool: string;
  dataset: number | null;
  event: number | null;
  status: JobStatus;
  output_artifact: number | null;
  output_artifact_url: string | null;
  log_relpath: string;
  created_at: string;
  finished_at: string | null;
}
// Result of the refinement-environment probe (CCP4 wired?) — gates the button.
export interface RefineAvailability {
  available: boolean;
  tool: string;
  resolved: string;
  reason: string;
}
export interface PanddaEvent {
  id: number;
  dataset: number;
  dtag: string;
  event_num: number;
  site_num: number | null;
  event_fraction: number | null;
  bdc: number | null;
  z_peak: number | null;
  z_mean: number | null;
  cluster_size: number | null;
  map_resolution: number | null;
  // Per-event autobuild metrics (PanDDA2 events.yaml Build:). build_score/rscc
  // quantify the fitted ligand pose; optimal_contour is the σ level it reads
  // best at (used to seed the contour slider). Null when no autobuild.
  build_score: number | null;
  rscc: number | null;
  optimal_contour: number | null;
  // Whether this event's autobuilt pose is ACCEPTED into the crystal model
  // (a LIG at its location is in the merged pandda-model.pdb), vs just a
  // candidate pose on disk. null = unknown (no merged model to compare).
  pose_merged: boolean | null;
  xyz_centroid: number[];
  xyz_peak: number[];
  decision: string;
  confidence: string;
  comment: string;
  inspected_by: string;
  inspected_at: string | null;
  artifacts: Artifact[];
  // Coordinates the viewer should load: the built/refined model when one
  // exists (event-scoped, else dataset-scoped), else null -> fall back to the
  // apo "structure" artifact. Surfaces the autobuilt ligand.
  current_model: Artifact | null;
  // The model-based-map MTZ (dimple at ingest, refined servalcat MTZ after
  // refinement). The client auto-reads it into 2mFo-DFc + mFo-DFc maps to judge
  // the current model. null -> no model-map source.
  current_sf: Artifact | null;
}
export interface Dataset {
  id: number;
  project: number;
  dtag: string;
  subtitle: string;
  analysed_resolution: number | null;
  r_free: number | null;
  map_uncertainty: number | null;
  events: PanddaEvent[];
  artifacts: Artifact[];
}

interface Paginated<T> {
  count: number;
  results: T[];
}

export type RunStatusValue =
  | "queued"
  | "provisioning"
  | "running"
  | "succeeded"
  | "failed"
  | "cancelled";

// A PanDDA run (the trigger→monitor→ingest lifecycle). The API emits ui_url
// pointing at /runs/<run_id>, which the SPA renders via the RunStatus page.
export interface Run {
  run_id: string;
  project: string; // external_id (Materia slug)
  project_id: number; // Reinspect PK, for linking to the review surface
  group: string;
  status: RunStatusValue;
  out_dir: string | null;
  failure_mode: string | null;
  failure_message: string | null;
  log_stream_url: string | null;
  progress: string | null; // e.g. "dataset 9/120"
  parent_run_id: number | null;
  ui_url: string;
}

async function get<T>(path: string): Promise<T> {
  const r = await fetch(`${BASE}${path}`);
  if (!r.ok) throw new Error(`${r.status} ${path}`);
  return r.json();
}

export const api = {
  listProjects: () => get<Paginated<Project>>("/projects/"),
  getProject: (id: number) => get<Project>(`/projects/${id}/`),
  projectReports: (id: number) => get<Artifact[]>(`/projects/${id}/reports/`),
  listDatasets: (projectName: string) =>
    get<Paginated<Dataset>>(
      `/datasets/?project=${encodeURIComponent(projectName)}&limit=500`
    ),
  listEvents: (projectName: string) =>
    get<Paginated<PanddaEvent>>(
      `/events/?project=${encodeURIComponent(projectName)}&limit=500`
    ),
  async setDecision(eventId: number, patch: Partial<PanddaEvent>) {
    const r = await fetch(`${BASE}/events/${eventId}/`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(patch),
    });
    if (!r.ok) throw new Error(`${r.status} PATCH event ${eventId}`);
    return r.json() as Promise<PanddaEvent>;
  },
  // Commit a client-edited crystal model as the dataset's current_model: the
  // client exports the live Moorhen model (ligand merge, deleted waters,
  // rotamers, alt-confs…) and POSTs it; the backend versions it origin=built
  // and repoints current_model. ``merge`` marks a ligand merge for this event
  // (sets pose_merged + auto-Hit); omit it for a generic save.
  async commitModel(eventId: number, pdb: string, opts?: { merge?: boolean }) {
    const r = await fetch(`${BASE}/events/${eventId}/commit_model/`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ pdb, merge: opts?.merge ?? false }),
    });
    const body = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(body.detail || `${r.status} commit failed`);
    return body as PanddaEvent;
  },
  async importZip(name: string, file: File) {
    const form = new FormData();
    form.append("name", name);
    form.append("file", file);
    const r = await fetch(`${BASE}/projects/import/`, {
      method: "POST",
      body: form,
    });
    const body = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(body.detail || `${r.status} import failed`);
    return body;
  },
  // Ingest a PanDDA output directory IN PLACE by server-side path (no copy).
  // Desktop-only: a server path is meaningful only when the backend runs on
  // the same machine (Electron/CLI). The endpoint itself is localhost-guarded.
  async ingestPath(name: string, path: string) {
    const r = await fetch(`${BASE}/projects/ingest_path/`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, path }),
    });
    const body = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(body.detail || `${r.status} ingest failed`);
    return body;
  },
  // --- jobs (refinement dispatch/tracking) ---
  // Is the refinement environment wired (CCP4 probe)? Gates the UI action.
  refineAvailable: () =>
    get<RefineAvailability>("/jobs/refine_available/"),
  // Dispatch a refinement of a dataset's current-best model. Returns the
  // queued/running Job; poll getJob until it leaves "running".
  async submitRefine(datasetId: number, params?: Record<string, unknown>) {
    const r = await fetch(`${BASE}/jobs/submit/`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ dataset: datasetId, params }),
    });
    const body = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(body.detail || `${r.status} submit failed`);
    return body as Job;
  },
  // Poll a job. GET also runs the server-side land-on-success step, so once
  // this returns status "succeeded" the dataset's current_model is repointed.
  getJob: (id: number) => get<Job>(`/jobs/${id}/`),

  // --- runs (PanDDA run lifecycle) ---
  // Poll a run. The GET also advances server-side status (and ingests on first
  // success), so once this returns "succeeded" the project holds the events.
  getRun: (id: string) => get<Run>(`/runs/${id}/`),

  // Absolute URL for streaming artifact bytes (Moorhen / iframe consume these).
  // download_url is server-emitted root-absolute (/api/v1/…); prefix it so it
  // routes through the mount when path-mounted (no-op when PREFIX is "").
  artifactUrl: (a: Artifact) => `${PREFIX}${a.download_url}`,
};
