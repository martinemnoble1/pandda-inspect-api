import type { Dataset, PanddaEvent } from "./api";

/**
 * The drawer groups events by an axis. Today: by dataset (one crystal and its
 * events). The shape is deliberately generic so a second axis — by site (a
 * recurrent binding location across many crystals) — is a small addition once
 * hit positions are validated. See README / the inspect drawer.
 */
export type GroupAxis = "dataset" | "site";

export interface EventGroup {
  key: string; // stable id for the group (dtag, or "site-N")
  title: string; // header label
  subtitle?: string; // secondary label (compound name, dataset count…)
  dataset?: Dataset; // present when grouping by dataset
  events: PanddaEvent[];
}

const decisionTally = (events: PanddaEvent[]) => {
  const hits = events.filter((e) => e.decision === "hit").length;
  const reviewed = events.filter((e) => e.decision !== "unreviewed").length;
  return { hits, reviewed, total: events.length };
};

export function summarise(events: PanddaEvent[]): string {
  const { hits, total } = decisionTally(events);
  const ev = `${total} event${total === 1 ? "" : "s"}`;
  return hits > 0 ? `${ev} · ${hits} hit${hits === 1 ? "" : "s"}` : ev;
}

/**
 * Bound-state occupancy estimate for one event: 1 − BDC. PanDDA's BDC is the
 * background (ground-state) fraction it subtracted to build the event map, so
 * (1 − BDC) is the fraction attributed to the bound state — a quick "how strong
 * is this hit" proxy. Null when BDC is missing.
 */
export const eventQuality = (ev: PanddaEvent): number | null =>
  ev.bdc != null ? 1 - ev.bdc : null;

/** Best (max) 1 − BDC across a group's events; null if none have a BDC. */
export const bestQuality = (events: PanddaEvent[]): number | null =>
  events.reduce<number | null>((m, e) => {
    const q = eventQuality(e);
    return q != null ? Math.max(m ?? 0, q) : m;
  }, null);

/**
 * True when this event has an autobuilt ligand pose — a per-event LIGAND_POSE
 * artifact (PanDDA2 fitted a ligand into THIS event's density). This is the
 * genuine per-event "built" signal; current_model is the per-crystal merged
 * model and would mark every event in an autobuilt dataset (see the
 * per-event-vs-crystal-model design note).
 */
export const eventIsBuilt = (ev: PanddaEvent): boolean =>
  ev.artifacts.some((a) => a.kind === "ligand_pose");

/** Dataset rolls up to "built" when any of its events has a pose. */
export const isAutobuilt = (events: PanddaEvent[]): boolean =>
  events.some(eventIsBuilt);

/**
 * A dataset is "fully rejected" when it has events and every one is no_hit —
 * i.e. you've triaged it and kept nothing. The 'Active' filter hides these.
 * An empty dataset is NOT fully rejected (nothing was rejected); the
 * has-events filter handles those separately.
 */
export const isAllNoHit = (events: PanddaEvent[]): boolean =>
  events.length > 0 && events.every((e) => e.decision === "no_hit");

/**
 * Dataset list filter. 'active' = worth looking at (has events, not all
 * rejected); 'withEvents' = has any events; 'all' = everything.
 */
export type DatasetFilter = "active" | "withEvents" | "all";

export const FILTER_LABELS: Record<DatasetFilter, string> = {
  active: "Active",
  withEvents: "With events",
  all: "All datasets",
};

/** Next filter in the cycle Active → With events → All → Active. */
export const nextFilter = (f: DatasetFilter): DatasetFilter =>
  f === "active" ? "withEvents" : f === "withEvents" ? "all" : "active";

export function applyFilter(
  datasets: Dataset[],
  filter: DatasetFilter
): Dataset[] {
  if (filter === "all") return datasets;
  const withEvents = datasets.filter((d) => d.events.length > 0);
  if (filter === "withEvents") return withEvents;
  return withEvents.filter((d) => !isAllNoHit(d.events)); // active
}

export type SortKey = "dtag" | "events" | "autobuilt" | "quality";

export const SORT_LABELS: Record<SortKey, string> = {
  dtag: "Name",
  events: "# events",
  autobuilt: "Autobuilt",
  quality: "Best quality",
};

/** Sort groups in place by the chosen key. Metrics sort descending (best/most
 * first); name sorts ascending, numeric-aware (x427 < x430 < x431…). */
export function sortGroups(groups: EventGroup[], key: SortKey): EventGroup[] {
  const byName = (a: EventGroup, b: EventGroup) =>
    a.title.localeCompare(b.title, undefined, { numeric: true });
  const sorted = [...groups];
  if (key === "dtag") return sorted.sort(byName);
  const metric = (g: EventGroup): number => {
    if (key === "events") return g.events.length;
    if (key === "autobuilt") return isAutobuilt(g.events) ? 1 : 0;
    return bestQuality(g.events) ?? -1; // quality
  };
  // Descending by metric, name as a stable tiebreak.
  return sorted.sort((a, b) => metric(b) - metric(a) || byName(a, b));
}

/**
 * The events of a set of groups in display order, flattened — the linear
 * sequence Prev/Next walks. Crossing a group boundary is implicit: the last
 * event of one group is followed by the first of the next.
 */
export const flattenEvents = (groups: EventGroup[]): PanddaEvent[] =>
  groups.flatMap((g) => g.events);

/**
 * Index of the event adjacent to `current` in `order`, stepping by `delta`
 * (+1 next, −1 prev). Returns null at the ends (no wraparound) or when the
 * current event isn't in the list (e.g. it was just filtered out).
 */
export function adjacentEvent(
  order: PanddaEvent[],
  current: PanddaEvent | null,
  delta: number
): PanddaEvent | null {
  if (!current) return order[0] ?? null;
  const i = order.findIndex((e) => e.id === current.id);
  if (i < 0) return order[0] ?? null;
  const j = i + delta;
  return j >= 0 && j < order.length ? order[j] : null;
}

/** Group a project's datasets/events along the chosen axis. */
export function groupEvents(
  datasets: Dataset[],
  axis: GroupAxis
): EventGroup[] {
  if (axis === "dataset") {
    return datasets.map((ds) => ({
      key: ds.dtag,
      title: ds.dtag,
      subtitle: ds.subtitle || undefined,
      dataset: ds,
      events: ds.events,
    }));
  }

  // axis === "site": collect events across all datasets, keyed by site_num.
  // (Meaningful once hits are validated; today site assignments are provisional.)
  const bySite = new Map<string, PanddaEvent[]>();
  for (const ds of datasets) {
    for (const ev of ds.events) {
      const key = ev.site_num == null ? "unassigned" : `site-${ev.site_num}`;
      const arr = bySite.get(key) ?? [];
      arr.push(ev);
      bySite.set(key, arr);
    }
  }
  return [...bySite.entries()]
    .sort((a, b) => a[0].localeCompare(b[0], undefined, { numeric: true }))
    .map(([key, events]) => {
      const datasetsInSite = new Set(events.map((e) => e.dtag)).size;
      return {
        key,
        title:
          key === "unassigned"
            ? "Unassigned"
            : `Site ${key.replace("site-", "")}`,
        subtitle: `${datasetsInSite} dataset${
          datasetsInSite === 1 ? "" : "s"
        }`,
        events,
      };
    });
}
