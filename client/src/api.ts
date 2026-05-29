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
  xyz_centroid: number[];
  xyz_peak: number[];
  decision: string;
  confidence: string;
  comment: string;
  inspected_by: string;
  inspected_at: string | null;
  artifacts: Artifact[];
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
      `/events/?limit=500`
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
  // Absolute URL for streaming artifact bytes (Moorhen / iframe consume these).
  artifactUrl: (a: Artifact) => a.download_url,
};
