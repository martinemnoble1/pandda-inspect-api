/**
 * The only thing the client knows about the backend: the REST contract.
 * No filesystem paths, no panddaPrefix — just typed calls to /api/v1.
 */
const BASE = "/api/v1";

export interface ProjectStatus {
  analysed: boolean;
  n_datasets: number;
  n_events: number;
  n_sites: number;
  n_hits: number;
  n_reviewed: number;
  hit_rate: number | null;
}
export interface Project {
  id: number;
  name: string;
  source_root: string;
  ingested_at: string;
  status: ProjectStatus;
}
export interface Artifact {
  id: number;
  kind: string;
  relpath: string;
  project: number | null;
  dataset: number | null;
  event: number | null;
  download_url: string;
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

  // Absolute URL for streaming artifact bytes (Moorhen / iframe consume these).
  artifactUrl: (a: Artifact) => a.download_url,
};
