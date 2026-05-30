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
