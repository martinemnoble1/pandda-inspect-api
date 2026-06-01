import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { RefObject } from "react";
import { useDispatch } from "react-redux";
import { addMap, addMolecule, removeMap, removeMolecule } from "moorhen";
import type { moorhen } from "moorhen/types/moorhen";
import {
  Accordion,
  AccordionDetails,
  AccordionSummary,
  Box,
  Button,
  Chip,
  CircularProgress,
  Divider,
  IconButton,
  MenuItem,
  Slider,
  Stack,
  TextField,
  ToggleButton,
  ToggleButtonGroup,
  Tooltip,
  Typography,
} from "@mui/material";
import ExpandMoreIcon from "@mui/icons-material/ExpandMore";
import ViewInArIcon from "@mui/icons-material/ViewInAr";
import CheckCircleIcon from "@mui/icons-material/CheckCircle";
import BuildCircleIcon from "@mui/icons-material/BuildCircle";
import NavigateBeforeIcon from "@mui/icons-material/NavigateBefore";
import NavigateNextIcon from "@mui/icons-material/NavigateNext";
import VisibilityIcon from "@mui/icons-material/Visibility";
import VisibilityOffIcon from "@mui/icons-material/VisibilityOff";
import store from "../store";
import {
  hideMap,
  newMap,
  newMolecule,
  recentre,
  setContourLevel,
  showMap,
  type MoorhenMapLike,
  type MoorhenMoleculeLike,
} from "../moorhen-shim";
import {
  api,
  type Artifact,
  type Dataset,
  type Job,
  type PanddaEvent,
  type RefineAvailability,
} from "../api";
import {
  adjacentEvent,
  applyFilter,
  bestQuality,
  eventPoseState,
  eventQuality,
  FILTER_LABELS,
  flattenEvents,
  groupEvents,
  hasCandidatePose,
  isAutobuilt,
  nextFilter,
  SORT_LABELS,
  sortGroups,
  summarise,
  type DatasetFilter,
  type GroupAxis,
  type SortKey,
} from "../grouping";
import { MolViewer } from "./MolViewer";

interface Props {
  projectName: string;
  glRef: RefObject<unknown>;
  commandCentre: RefObject<moorhen.CommandCentre | null>;
  cootInitialized: boolean;
}

// Default contour level (in σ) for PanDDA event maps. BDC correction inflates
// the bound-state density, so ~2σ isolates the binding event where 1σ shows too
// much bulk. The ideal level is dataset/event-dependent — this is just the
// starting point; the slider lets the user retune.
const DEFAULT_EVENT_SIGMA = 2.0;
// Default contour (σ) for a 2mFo-DFc direct map and an mFo-DFc difference map.
const DEFAULT_2FOFC_SIGMA = 1.5;
const DEFAULT_FOFC_SIGMA = 3.0;

// A map currently loaded in the viewer, with the UI state to contour + toggle
// it. ``map`` is the live MoorhenMap (for RMSD-aware σ→absolute conversion);
// the rest drives the per-map control row.
interface LoadedMap {
  map: MoorhenMapLike;
  molNo: number;
  label: string;
  sigma: number; // current contour in σ
  isDifference: boolean;
  visible: boolean;
}

const artifactOf = (ev: PanddaEvent, kind: string): Artifact | undefined =>
  ev.artifacts.find((a) => a.kind === kind);

const decisionColour = (d: string) =>
  d === "hit" ? "success" : d === "no_hit" ? "error" : "default";

export function InspectDrawer({
  projectName,
  glRef,
  commandCentre,
  cootInitialized,
}: Props) {
  const dispatch = useDispatch();
  const [datasets, setDatasets] = useState<Dataset[]>([]);
  const [axis, setAxis] = useState<GroupAxis>("dataset");
  const [sort, setSort] = useState<SortKey>("dtag");
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState<DatasetFilter>("active");
  const [expanded, setExpanded] = useState<string | false>(false);
  const [loadingId, setLoadingId] = useState<number | null>(null);
  const [selected, setSelected] = useState<PanddaEvent | null>(null);
  // All maps loaded for the live event (event map + model-based 2Fo-Fc/Fo-Fc),
  // each with its own contour + visibility — drives the per-map control rows.
  const [maps, setMaps] = useState<LoadedMap[]>([]);
  // Refinement is CRYSTAL-scoped (acts on the dataset's current_model vs its
  // MTZ; legacy pandda.inspect + DESIGN §1.2). refineAvail gates the action on
  // the CCP4 probe. Jobs are tracked PER DATASET (not per selected event) so a
  // refinement is non-modal background work: submit, then navigate/inspect/
  // refine other crystals freely while it runs. Keyed by dataset id.
  const [refineAvail, setRefineAvail] =
    useState<RefineAvailability | null>(null);
  const [jobsByDataset, setJobsByDataset] = useState<Record<number, Job>>({});
  const loadedDtag = useRef<string | null>(null);
  // The per-event autobuilt ligand pose overlaid as its own molecule (a
  // CANDIDATE proposal, distinct from the merged model). Cleared on every event
  // switch — unlike the per-crystal model, the pose is event-specific.
  const poseMolRef = useRef<MoorhenMoleculeLike | null>(null);
  // The per-crystal model molecule (kept across event switches within a
  // dataset) — the merge target + what we export to persist a build.
  const modelMolRef = useRef<MoorhenMoleculeLike | null>(null);
  const [merging, setMerging] = useState(false);
  // Live mirrors of selection + loadEvent so a detached background poll (a
  // refinement landing later, after you've navigated away) reads CURRENT
  // values, not the stale closure from when it was submitted.
  const selectedRef = useRef<PanddaEvent | null>(null);
  selectedRef.current = selected;
  const loadEventRef = useRef<((ev: PanddaEvent) => void) | null>(null);

  useEffect(() => {
    if (!projectName) return;
    api
      .listDatasets(projectName)
      .then((d) => setDatasets(d.results))
      .catch(() => setDatasets([]));
  }, [projectName]);

  // Probe once whether the refinement environment is wired (CCP4). Gates the
  // Refine action; null while unknown, then the probe result.
  useEffect(() => {
    api.refineAvailable().then(setRefineAvail).catch(() => setRefineAvail(null));
  }, []);

  // Delete every map currently in the store (state.maps is an array in 0.23).
  const clearMaps = useCallback(async () => {
    const storeMaps: any[] = (store.getState() as any).maps ?? [];
    for (const mp of storeMaps) {
      await mp.delete();
      dispatch(removeMap(mp));
    }
    setMaps([]);
  }, [dispatch]);

  // Drop the per-event pose overlay (if any). Called on every event switch.
  const clearPose = useCallback(async () => {
    const pose = poseMolRef.current;
    if (pose) {
      await pose.delete();
      dispatch(removeMolecule(pose as any));
      poseMolRef.current = null;
    }
  }, [dispatch]);

  // Full teardown: maps + molecules. Used when switching dataset.
  const clearLoaded = useCallback(async () => {
    await clearMaps();
    const molecules: any[] =
      (store.getState() as any).molecules.moleculeList ?? [];
    for (const m of molecules) {
      await m.delete();
      dispatch(removeMolecule(m));
    }
    poseMolRef.current = null; // molecules just bulk-deleted above
    modelMolRef.current = null;
  }, [dispatch, clearMaps]);

  const loadEvent = useCallback(
    async (ev: PanddaEvent) => {
      const cc = commandCentre.current as
        | (moorhen.CommandCentre & { cootCommand?: unknown })
        | null;
      if (!cootInitialized || !glRef.current || !cc?.cootCommand) return;
      setLoadingId(ev.id);
      try {
        if (loadedDtag.current !== ev.dtag) {
          // New dataset: tear down everything and (re)load its model.
          await clearLoaded();
          loadedDtag.current = ev.dtag;

          // Load current_model: the APO input at ingest (ligand-free base),
          // which then ACCUMULATES accepted ligands as you merge poses /
          // refine (origin=built/refined). So this shows your built-up work;
          // fall back to the imported apo "structure" artifact if unset.
          const model = ev.current_model ?? artifactOf(ev, "structure");
          if (model) {
            const mol = newMolecule(commandCentre, store);
            await mol.loadToCootFromURL(api.artifactUrl(model), ev.dtag);
            // Load the ligand restraint dictionary so the LIG residue bonds and
            // refines correctly. Without it, Moorhen auto-fetches
            // monomers/l/LIG.cif (a 404) and draws bare atoms. Our dict is
            // embedded in the DB (data/<dtag>/ligand.cif) and served as text.
            const lig = artifactOf(ev, "ligand");
            let dictLoaded = false;
            if (lig) {
              try {
                const cif = await fetch(api.artifactUrl(lig)).then((r) =>
                  r.ok ? r.text() : ""
                );
                if (cif) {
                  await mol.addDict(cif);
                  dictLoaded = true;
                }
              } catch {
                // Non-fatal: fall back to bare-atom rendering.
              }
            }
            await mol.addRepresentation("CBs", "/*/*");
            // addDict does NOT redraw, so the first draw above perceives bonds
            // without the dict (all single bonds). Re-perceive WITH the dict so
            // aromatic/double orders render — the proven 0.23 dirty+redraw.
            if (dictLoaded) {
              mol.setAtomsDirty(true);
              await mol.fetchIfDirtyAndDraw("CBs");
            }
            dispatch(addMolecule(mol as any));
            modelMolRef.current = mol;
          }
        } else {
          // Same dataset, different event: keep the model, but drop the old
          // event map + pose so they don't accumulate as you step through
          // events (the pose is per-event; the model is per-crystal).
          await clearMaps();
          await clearPose();
        }

        // Recentre on the event. recentre() dispatches setOrigin (the Redux
        // source of truth that MoorhenMap.drawMapContour reads, so the map
        // re-contours at the new centre) and nudges the GL camera. Done BEFORE
        // loading the map so the map's first contour lands on the event.
        if (ev.xyz_centroid?.length === 3) {
          recentre(
            dispatch,
            glRef as { current: unknown },
            ev.xyz_centroid as [number, number, number]
          );
        }

        // Accumulate the maps loaded for this event (event + model-based),
        // each with its contour/visibility UI state; committed via setMaps.
        const loaded: LoadedMap[] = [];

        const emap = artifactOf(ev, "event_map");
        if (emap) {
          const map = newMap(commandCentre, store);
          // PanDDA2 emits event maps as CCP4 real-space maps; PanDDA1 emitted
          // them as MTZ reflection files (with FEVENT/PHEVENT columns). Branch
          // on the artifact's extension so both ingests work — the import
          // boundary changed the format, not the contract.
          const isCcp4 = /\.(ccp4|map|mrc)$/i.test(emap.relpath);
          if (isCcp4) {
            await map.loadToCootFromMapURL(
              api.artifactUrl(emap),
              `${ev.dtag}-EVENT`,
              false
            );
          } else {
            await map.loadToCootFromMtzURL(
              api.artifactUrl(emap),
              `${ev.dtag}-EVENT`,
              { F: "FEVENT", PHI: "PHEVENT", useWeight: false, isDifference: false }
            );
          }
          // PanDDA event maps are real-space CCP4 maps read directly (not
          // MTZ→FFT). Moorhen's direct-map load runs is_EM_map, and a PanDDA box
          // can trip it → isOriginLocked=true → doCootContour IGNORES the GL
          // origin and contours at the cell centre (MoorhenMap.doCootContour).
          // That pins the density at a fixed spot regardless of setOrigin, which
          // is exactly the "won't centre / won't track on pan" symptom. These
          // are crystallographic event maps, not cryo-EM: unlock so the contour
          // follows the origin like a normal X-ray map.
          map.isEM = false;
          map.isOriginLocked = false;
          // Contour level: Coot's contour API works in ABSOLUTE map units, so a
          // sigma level must be multiplied by the map RMSD (Moorhen's own
          // default-contour logic does exactly this — MoorhenMapManager).
          // Passing a bare 1.0 absolute (as before) gives an arbitrary level for
          // any map whose RMSD isn't ~1, which is why event maps looked wrong.
          //
          // PanDDA event maps are BDC-corrected: the bound-state ligand density
          // is restored toward full occupancy, so they are viewed like a normal
          // 2Fo-Fc map (single positive contour) — NOT like an Fo-Fc difference
          // map at ±3σ. Hence isDifference stays false. Default 2σ: BDC
          // correction inflates contrast, so 1σ shows too much bulk; ~2σ
          // isolates the binding-event density (matches pandda.inspect practice
          // for this BAZ2B data). The right level varies by dataset/event, so
          // the user can retune via the slider.
          // Prefer this event's autobuild-tuned contour (events.yaml "Optimal
          // Contour", in σ) when present — the level the fitted ligand reads
          // best at — else the generic BDC default. The slider still retunes.
          const sigma =
            ev.optimal_contour != null && ev.optimal_contour > 0
              ? ev.optimal_contour
              : DEFAULT_EVENT_SIGMA;
          const level =
            typeof map.mapRmsd === "number" && map.mapRmsd > 0
              ? sigma * map.mapRmsd
              : map.contourLevel ?? 1.0;
          dispatch(addMap(map as any));
          // NB: deliberately NOT setActiveMap here. Making this the active map
          // mounts Moorhen's MapScrollWheelListener (MoorhenMapManager gates it
          // on isMapActive), which reads map.mapCentre[0] unconditionally — and
          // a freshly-loaded CCP4 map has mapCentre=null, crashing the render
          // tree. We don't need the active map for inspect+contour: contour is
          // dispatched by molNo (below), and the view follows the camera origin.
          // The active map is a refinement-target concern — set it in #4 (ligand
          // build / refine), where we'll also populate mapCentre properly.
          // Set the level via Redux — MoorhenMapManager re-contours off the
          // `contourLevels` slice, NOT off map.contourLevel (see shim note).
          dispatch(setContourLevel({ molNo: map.molNo, contourLevel: level }));
          loaded.push({
            map,
            molNo: map.molNo,
            label: "Event",
            sigma,
            isDifference: false,
            visible: true,
          });
        }

        // Model-based maps from current_sf (dimple MTZ at first, refined
        // servalcat MTZ after refinement): the 2mFo-DFc + mFo-DFc maps for
        // judging the CURRENT model, alongside the event map. We pass the
        // EXPLICIT declared columns (no Coot heuristics — we own the
        // convention; see map-of-record). Cleaned by clearMaps each switch.
        const sf = ev.current_sf;
        if (sf && sf.map_columns?.length) {
          for (const col of sf.map_columns) {
            const mmap = newMap(commandCentre, store);
            await mmap.loadToCootFromMtzURL(api.artifactUrl(sf), sf.relpath, {
              F: col.F,
              PHI: col.PHI,
              isDifference: col.isDifference,
              useWeight: false,
            });
            const msigma = col.isDifference
              ? DEFAULT_FOFC_SIGMA
              : DEFAULT_2FOFC_SIGMA;
            const mlevel =
              typeof mmap.mapRmsd === "number" && mmap.mapRmsd > 0
                ? msigma * mmap.mapRmsd
                : msigma;
            dispatch(addMap(mmap as any));
            dispatch(
              setContourLevel({ molNo: mmap.molNo, contourLevel: mlevel })
            );
            loaded.push({
              map: mmap,
              molNo: mmap.molNo,
              label: col.isDifference ? "Fo-Fc" : "2Fo-Fc",
              sigma: msigma,
              isDifference: col.isDifference,
              visible: true,
            });
          }
        }
        setMaps(loaded);

        // Overlay THIS event's autobuilt candidate pose as its own molecule —
        // only while it's NOT yet merged. pose_merged is now reliable (the apo
        // base means nothing is pre-merged; the merge action is the only thing
        // that sets it True), so once you merge a pose it stops overlaying and
        // appears in the loaded model instead — no duplicate.
        const pose =
          ev.pose_merged !== true
            ? artifactOf(ev, "ligand_pose")
            : undefined;
        if (pose) {
          const pmol = newMolecule(commandCentre, store);
          await pmol.loadToCootFromURL(
            api.artifactUrl(pose),
            `${ev.dtag}-pose-${ev.event_num}`
          );
          // Bond the LIG with its dict (same embedded CIF as the model).
          const lig = artifactOf(ev, "ligand");
          if (lig) {
            try {
              const cif = await fetch(api.artifactUrl(lig)).then((r) =>
                r.ok ? r.text() : ""
              );
              if (cif) await pmol.addDict(cif);
            } catch {
              /* non-fatal: bare-atom pose */
            }
          }
          await pmol.addRepresentation("CBs", "/*/*");
          pmol.setAtomsDirty(true);
          await pmol.fetchIfDirtyAndDraw("CBs");
          dispatch(addMolecule(pmol as any));
          poseMolRef.current = pmol;
        }

        setSelected(ev);
      } finally {
        setLoadingId(null);
      }
    },
    [
      glRef,
      commandCentre,
      cootInitialized,
      dispatch,
      clearLoaded,
      clearMaps,
      clearPose,
    ]
  );
  loadEventRef.current = loadEvent;

  // Contour ONE of the loaded maps (by molNo). Slider is in σ; Coot contours
  // in ABSOLUTE units, so multiply by that map's RMSD. Dispatch — the
  // MapManager redraws off the Redux contourLevels slice (poking
  // map.contourLevel + drawMapContour does not re-render).
  const onContour = useCallback(
    (molNo: number, sigma: number) => {
      setMaps((prev) =>
        prev.map((m) => (m.molNo === molNo ? { ...m, sigma } : m))
      );
      const m = maps.find((x) => x.molNo === molNo);
      const rmsd = m?.map.mapRmsd;
      const level =
        typeof rmsd === "number" && rmsd > 0 ? sigma * rmsd : sigma;
      dispatch(setContourLevel({ molNo, contourLevel: level }));
    },
    [dispatch, maps]
  );

  // Toggle a map's visibility (Redux-driven; MoorhenMapManager shows/hides off
  // the visibleMaps slice). Lets the user declutter a dense 3-map view.
  const onToggleVisible = useCallback(
    (molNo: number) => {
      setMaps((prev) =>
        prev.map((m) => {
          if (m.molNo !== molNo) return m;
          const visible = !m.visible;
          dispatch(visible ? showMap(m.map) : hideMap(m.map));
          return { ...m, visible };
        })
      );
    },
    [dispatch]
  );

  const setDecision = useCallback(
    async (ev: PanddaEvent, decision: string) => {
      const updated = await api.setDecision(ev.id, { decision });
      setDatasets((prev) =>
        prev.map((ds) => ({
          ...ds,
          events: ds.events.map((e) =>
            e.id === ev.id ? { ...e, ...updated } : e
          ),
        }))
      );
      setSelected((s) => (s && s.id === ev.id ? { ...s, ...updated } : s));
    },
    []
  );

  // Refine the WHOLE CRYSTAL — NON-MODAL. Submit a servalcat Job for this
  // dataset, record it in jobsByDataset, and poll in the BACKGROUND. The submit
  // returns immediately so you can navigate / inspect / refine other crystals
  // while it runs; status is shown per-dataset (header chip + button). When the
  // job lands, that dataset's events are refreshed in place, and the live model
  // is reloaded ONLY if you're currently viewing that crystal (refs, not stale
  // closure). Crystal-scoped: events feed this one model; you don't refine one.
  const pollRefineJob = useCallback(
    async (datasetId: number, dtag: string, jobId: number) => {
      for (let i = 0; i < 600; i++) {
        await new Promise((r) => setTimeout(r, 2000));
        let job: Job;
        try {
          job = await api.getJob(jobId);
        } catch {
          continue; // transient; keep polling
        }
        setJobsByDataset((m) => ({ ...m, [datasetId]: job }));
        if (job.status === "running") continue;
        if (job.status === "succeeded") {
          // current_model now points at the refined model — refresh this
          // dataset's rows in place (not the whole project).
          try {
            const fresh = await api.listDatasets(projectName);
            const fd = fresh.results.find((d) => d.id === datasetId);
            if (fd) {
              setDatasets((prev) =>
                prev.map((d) => (d.id === datasetId ? fd : d))
              );
              // Reload the 3D model ONLY if you're still on the SAME event of
              // this crystal — reload exactly that event so the refined coords
              // show. If you've navigated elsewhere (or the event is gone),
              // leave the view as-is: never fall back to events[0], which used
              // to dump the view onto event 1 after a refine.
              const sel = selectedRef.current;
              const stillHere =
                loadedDtag.current === dtag &&
                sel != null &&
                fd.events.some((e) => e.id === sel.id);
              if (stillHere) {
                const ev = fd.events.find((e) => e.id === sel!.id)!;
                loadedDtag.current = null; // force coords re-pull
                loadEventRef.current?.(ev);
              }
            }
          } catch {
            /* leave status succeeded; next nav re-fetches */
          }
        }
        return; // terminal (succeeded/failed) — stop polling
      }
    },
    [projectName]
  );

  const refineCrystal = useCallback(
    (ev: PanddaEvent) => {
      const existing = jobsByDataset[ev.dataset];
      if (existing && existing.status === "running") return;
      api
        .submitRefine(ev.dataset)
        .then((job) => {
          setJobsByDataset((m) => ({ ...m, [ev.dataset]: job }));
          pollRefineJob(ev.dataset, ev.dtag, job.id);
        })
        .catch(() => {
          // Surface a synthetic failed status for this dataset.
          setJobsByDataset((m) => ({
            ...m,
            [ev.dataset]: {
              id: -1, tool: "servalcat", dataset: ev.dataset, event: null,
              status: "failed", output_artifact: null,
              output_artifact_url: null, log_relpath: "",
              created_at: "", finished_at: null,
            },
          }));
        });
    },
    [jobsByDataset, pollRefineJob]
  );

  // Merge this event's candidate pose INTO the crystal model — client-side in
  // Coot (merge_molecules), then persist the merged model through the API so
  // the canonical record updates (no drift). The merge is the hit assertion.
  // Export the live model molecule and commit it as the dataset's
  // current_model (origin=built). The shared tail of merge + generic save.
  const commitLiveModel = useCallback(
    async (ev: PanddaEvent, merge: boolean) => {
      const model = modelMolRef.current;
      if (!model) return;
      const pdb = await model.getAtoms("pdb");
      const updated = await api.commitModel(ev.id, pdb, { merge });
      setDatasets((prev) =>
        prev.map((ds) => ({
          ...ds,
          events: ds.events.map((e) =>
            e.id === ev.id ? { ...e, ...updated } : e
          ),
        }))
      );
      setSelected((s) => (s && s.id === ev.id ? { ...s, ...updated } : s));
    },
    []
  );

  const mergePose = useCallback(
    async (ev: PanddaEvent) => {
      const cc = commandCentre.current as
        | (moorhen.CommandCentre & { cootCommand?: any })
        | null;
      const model = modelMolRef.current;
      const pose = poseMolRef.current;
      if (!cc?.cootCommand || !model || !pose) return;
      setMerging(true);
      try {
        // Coot merges the pose molecule into the model molecule (the prototype
        // recipe). The pose then lives in the model; drop the standalone pose.
        await cc.cootCommand(
          {
            returnType: "status",
            command: "merge_molecules",
            commandArgs: [model.molNo, `${pose.molNo}`],
          },
          true
        );
        model.setAtomsDirty(true);
        await model.fetchIfDirtyAndDraw("CBs");
        await clearPose();
        // Persist the merged model (merge=true -> pose_merged + auto-Hit).
        await commitLiveModel(ev, true);
      } catch {
        /* surfaced via the merging flag clearing; non-fatal */
      } finally {
        setMerging(false);
      }
    },
    [commandCentre, clearPose, commitLiveModel]
  );

  // Generic "Save model edits": commit whatever the user has changed in
  // Moorhen (deleted waters, rotamers, alt-confs/occupancy, …) as the new
  // current_model — origin=built, no hit assertion (a plain edit isn't one).
  const saveModel = useCallback(
    async (ev: PanddaEvent) => {
      if (!modelMolRef.current) return;
      setMerging(true);
      try {
        await commitLiveModel(ev, false);
      } catch {
        /* non-fatal */
      } finally {
        setMerging(false);
      }
    },
    [commitLiveModel]
  );

  // The dataset whose event is currently live in Moorhen — its ligand sketch
  // is the one worth showing (detail tied to "what am I looking at").
  const liveDataset = useMemo(
    () =>
      selected
        ? datasets.find((d) => d.dtag === selected.dtag) ?? null
        : null,
    [selected, datasets]
  );
  const liveLigand = liveDataset?.artifacts.find((a) => a.kind === "ligand");

  const groups = useMemo(() => {
    const visible = applyFilter(datasets, filter);
    const grouped = groupEvents(visible, axis);
    const q = search.trim().toLowerCase();
    const filtered = !q
      ? grouped
      : grouped.filter(
          (g) =>
            g.title.toLowerCase().includes(q) ||
            g.subtitle?.toLowerCase().includes(q)
        );
    return sortGroups(filtered, sort);
  }, [datasets, axis, filter, search, sort]);

  // The linear event sequence Prev/Next walks — exactly what's on screen, in
  // display order, across dataset boundaries. Recomputed when the list changes
  // (e.g. a dataset drops out after its last event is marked no_hit).
  const eventOrder = useMemo(() => flattenEvents(groups), [groups]);
  const navIndex = useMemo(
    () =>
      selected ? eventOrder.findIndex((e) => e.id === selected.id) : -1,
    [eventOrder, selected]
  );
  const prevEvent = adjacentEvent(eventOrder, selected, -1);
  const nextEvent = adjacentEvent(eventOrder, selected, +1);

  // Keep the accordion in step with the live event: open the group the
  // selected event belongs to and collapse any other (single-open). This makes
  // the list reflect "where am I" when prev/next crosses a dataset boundary —
  // and when clicking a chip in a collapsed group. Grouping by dataset keys on
  // dtag; by site keys on "site-N" / "unassigned" (see grouping.ts).
  useEffect(() => {
    if (!selected) return;
    const key =
      axis === "dataset"
        ? selected.dtag
        : selected.site_num == null
        ? "unassigned"
        : `site-${selected.site_num}`;
    setExpanded((cur) => (cur === key ? cur : key));
  }, [selected, axis]);
  const goAdjacent = useCallback(
    (delta: number) => {
      const target = adjacentEvent(eventOrder, selected, delta);
      if (target) loadEvent(target);
    },
    [eventOrder, selected, loadEvent]
  );

  return (
    <Box
      sx={{
        // Fill the host side-panel rather than a fixed 380px column (which left
        // the right of the wider Moorhen panel empty). minWidth keeps it usable
        // if the panel is ever dragged narrow.
        width: "100%",
        minWidth: 320,
        height: "100%",
        display: "flex",
        flexDirection: "column",
        boxSizing: "border-box",
      }}
    >
      {/* Controls */}
      <Box sx={{ p: 1, flexShrink: 0 }}>
        <Stack
          direction="row"
          spacing={1}
          alignItems="center"
          justifyContent="space-between"
        >
          <Typography variant="subtitle1">{projectName}</Typography>
          <ToggleButtonGroup
            size="small"
            exclusive
            value={axis}
            onChange={(_, v) => v && setAxis(v)}
          >
            <ToggleButton value="dataset">Dataset</ToggleButton>
            <ToggleButton value="site">Site</ToggleButton>
          </ToggleButtonGroup>
        </Stack>
        {!cootInitialized && (
          <Typography variant="caption" color="text.secondary">
            Waiting for Moorhen to finish loading…
          </Typography>
        )}
        <Stack direction="row" spacing={1} sx={{ mt: 1 }}>
          <TextField
            size="small"
            fullWidth
            placeholder={
              axis === "dataset" ? "Filter datasets…" : "Filter sites…"
            }
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
          <TextField
            select
            size="small"
            label="Sort"
            value={sort}
            onChange={(e) => setSort(e.target.value as SortKey)}
            sx={{ minWidth: 130, flexShrink: 0 }}
          >
            {(Object.keys(SORT_LABELS) as SortKey[]).map((k) => (
              <MenuItem key={k} value={k}>
                {SORT_LABELS[k]}
              </MenuItem>
            ))}
          </TextField>
        </Stack>
        <Box sx={{ mt: 0.5 }}>
          <Tooltip
            title={
              filter === "active"
                ? "Showing datasets with events that aren't all marked No hit — click to widen"
                : filter === "withEvents"
                ? "Showing all datasets with events — click to show every dataset"
                : "Showing every dataset — click to return to Active"
            }
            arrow
          >
            <Chip
              size="small"
              label={FILTER_LABELS[filter]}
              onClick={() => setFilter((f) => nextFilter(f))}
              variant={filter === "all" ? "outlined" : "filled"}
              color={filter === "all" ? "default" : "primary"}
            />
          </Tooltip>
        </Box>
      </Box>

      {/* Grouped accordion */}
      <Box sx={{ flex: 1, overflow: "auto", minHeight: 0 }}>
        {groups.map((g) => {
          const isLiveGroup =
            axis === "dataset" && selected?.dtag === g.key;
          return (
            <Accordion
              key={g.key}
              disableGutters
              expanded={expanded === g.key}
              onChange={(_, isOpen) => setExpanded(isOpen ? g.key : false)}
            >
              <AccordionSummary expandIcon={<ExpandMoreIcon />}>
                <Box sx={{ width: "100%" }}>
                  <Stack
                    direction="row"
                    spacing={1}
                    alignItems="center"
                    flexWrap="wrap"
                  >
                    <Typography sx={{ fontWeight: 600 }}>{g.title}</Typography>
                    {isLiveGroup && (
                      <Chip size="small" color="warning" label="viewing" />
                    )}
                  </Stack>
                  <Typography variant="caption" color="text.secondary">
                    {g.subtitle ? `${g.subtitle} · ` : ""}
                    {summarise(g.events)}
                  </Typography>
                  <Stack
                    direction="row"
                    spacing={0.5}
                    sx={{ mt: 0.5 }}
                    flexWrap="wrap"
                    useFlexGap
                  >
                    {(() => {
                      // Triage signals computed from the group's events.
                      const nEvents = g.events.length;
                      const nHits = g.events.filter(
                        (e) => e.decision === "hit"
                      ).length;
                      const built = isAutobuilt(g.events);
                      const candidate = !built && hasCandidatePose(g.events);
                      const topQ = bestQuality(g.events);
                      return (
                        <>
                          <Tooltip title="Number of PanDDA events" arrow>
                            <Chip
                              size="small"
                              variant="outlined"
                              label={`${nEvents} event${
                                nEvents === 1 ? "" : "s"
                              }`}
                            />
                          </Tooltip>
                          {built && (
                            <Tooltip
                              title={
                                "A ligand is built into this crystal's model"
                              }
                              arrow
                            >
                              <Chip
                                size="small"
                                color="info"
                                icon={<BuildCircleIcon />}
                                label="built"
                              />
                            </Tooltip>
                          )}
                          {candidate && (
                            <Tooltip
                              title={
                                "Autobuilt ligand pose(s) proposed but not " +
                                "yet merged into the model"
                              }
                              arrow
                            >
                              <Chip
                                size="small"
                                variant="outlined"
                                color="info"
                                icon={<BuildCircleIcon />}
                                label="candidate"
                              />
                            </Tooltip>
                          )}
                          {(() => {
                            // Per-crystal refine status — visible here even
                            // while you're inspecting another crystal's events
                            // (refinement is non-modal background work).
                            const j = g.dataset
                              ? jobsByDataset[g.dataset.id]
                              : undefined;
                            if (!j) return null;
                            if (j.status === "running") {
                              return (
                                <Chip
                                  size="small"
                                  color="warning"
                                  icon={<CircularProgress size={12} />}
                                  label="refining"
                                />
                              );
                            }
                            if (j.status === "succeeded") {
                              return (
                                <Chip
                                  size="small"
                                  variant="outlined"
                                  color="success"
                                  label="refined"
                                />
                              );
                            }
                            if (j.status === "failed") {
                              return (
                                <Chip
                                  size="small"
                                  variant="outlined"
                                  color="error"
                                  label="refine failed"
                                />
                              );
                            }
                            return null;
                          })()}
                          {nHits > 0 && (
                            <Chip
                              size="small"
                              color="success"
                              label={`${nHits} hit${nHits === 1 ? "" : "s"}`}
                            />
                          )}
                          {topQ != null && (
                            <Tooltip
                              title="Best bound-state occupancy in this dataset (1 − BDC) — a quick measure of the strongest hit"
                              arrow
                            >
                              <Chip
                                size="small"
                                variant="outlined"
                                color={topQ >= 0.4 ? "primary" : "default"}
                                label={`Q ${Math.round(topQ * 100)}%`}
                              />
                            </Tooltip>
                          )}
                        </>
                      );
                    })()}
                    {g.dataset?.analysed_resolution != null && (
                      <Chip
                        size="small"
                        variant="outlined"
                        label={`res ${g.dataset.analysed_resolution}`}
                      />
                    )}
                    {g.dataset?.r_free != null && (
                      <Chip
                        size="small"
                        variant="outlined"
                        label={`Rfree ${g.dataset.r_free.toFixed(3)}`}
                      />
                    )}
                  </Stack>
                </Box>
              </AccordionSummary>
              <AccordionDetails>
                {/* Ligand sketch only for the dataset currently live in Moorhen */}
                {isLiveGroup && liveLigand && (
                  <Box sx={{ mb: 1, textAlign: "center" }}>
                    <MolViewer cifUrl={api.artifactUrl(liveLigand)} />
                  </Box>
                )}
                {/* One-line legend so the chip encoding is self-explaining. */}
                <Typography
                  variant="caption"
                  color="text.secondary"
                  sx={{ display: "block", mb: 0.5 }}
                >
                  Click an event to view it in 3D · label is{" "}
                  {axis === "site"
                    ? "crystal : event"
                    : "event · quality (1 − BDC)"}
                </Typography>
                <Stack direction="row" spacing={0.75} flexWrap="wrap" useFlexGap>
                  {g.events.map((ev) => {
                    const isLive = selected?.id === ev.id;
                    const q = eventQuality(ev);
                    const poseState = eventPoseState(ev);
                    const merged = poseState === "merged";
                    const candidate = poseState === "candidate";
                    const occ =
                      ev.event_fraction != null
                        ? `${Math.round(ev.event_fraction * 100)}%`
                        : "—";
                    const qStr =
                      q != null ? `${Math.round(q * 100)}%` : "—";
                    const label =
                      axis === "site"
                        ? `${ev.dtag}:${ev.event_num}`
                        : `Event ${ev.event_num} · ${qStr}`;
                    const tip = (
                      <Box sx={{ fontSize: 12, lineHeight: 1.5 }}>
                        <div>
                          <strong>
                            {ev.dtag} · event {ev.event_num}
                          </strong>
                        </div>
                        <div>Quality (1 − BDC): {qStr}</div>
                        <div>Event fraction: {occ}</div>
                        <div>Z-peak: {ev.z_peak?.toFixed(1) ?? "—"}</div>
                        <div>BDC: {ev.bdc ?? "—"}</div>
                        <div>Cluster size: {ev.cluster_size ?? "—"}</div>
                        <div>Site: {ev.site_num ?? "—"}</div>
                        <div
                          style={{
                            marginTop: 4,
                            color:
                              poseState !== "none" ? "#4fc3f7" : undefined,
                          }}
                        >
                          {merged
                            ? `Ligand built into model · RSCC ${
                                ev.rscc?.toFixed(2) ?? "—"
                              }`
                            : candidate
                            ? `Candidate pose (not merged) · RSCC ${
                                ev.rscc?.toFixed(2) ?? "—"
                              }`
                            : "No autobuilt ligand"}
                        </div>
                        <div style={{ marginTop: 4, opacity: 0.8 }}>
                          {cootInitialized
                            ? "Click to load structure + event map"
                            : "Waiting for Moorhen…"}
                        </div>
                      </Box>
                    );
                    return (
                      <Tooltip key={ev.id} title={tip} arrow placement="top">
                        {/* span wrapper so Tooltip works on a disabled chip */}
                        <span>
                          <Chip
                            clickable={cootInitialized}
                            disabled={!cootInitialized}
                            onClick={() => loadEvent(ev)}
                            variant={isLive ? "filled" : "outlined"}
                            color={
                              isLive ? "warning" : decisionColour(ev.decision)
                            }
                            icon={
                              loadingId === ev.id ? (
                                <CircularProgress size={14} />
                              ) : ev.decision === "hit" ? (
                                <CheckCircleIcon />
                              ) : poseState !== "none" ? (
                                // A built/candidate ligand backs this event —
                                // flag it with the build icon (solid for
                                // merged, outlined-tint for candidate via sx).
                                <BuildCircleIcon />
                              ) : (
                                <ViewInArIcon />
                              )
                            }
                            label={label}
                            sx={{
                              fontWeight: isLive ? 700 : 500,
                              transition: "transform 80ms ease",
                              "&:hover": { transform: "translateY(-1px)" },
                              // Built events get a solid accent edge + tint so
                              // they stand out among unbuilt candidates without
                              // stealing the decision colour (hit/no-hit) or the
                              // live "viewing" highlight. Merged = solid accent;
                              // candidate = dashed accent (proposed, not in
                              // model yet).
                              ...(merged && !isLive
                                ? {
                                    borderColor: "info.main",
                                    borderWidth: 1.5,
                                    bgcolor: "rgba(79,195,247,0.08)",
                                  }
                                : candidate && !isLive
                                ? {
                                    borderColor: "info.main",
                                    borderStyle: "dashed",
                                    bgcolor: "rgba(79,195,247,0.03)",
                                  }
                                : {}),
                            }}
                          />
                        </span>
                      </Tooltip>
                    );
                  })}
                </Stack>
              </AccordionDetails>
            </Accordion>
          );
        })}
        {groups.length === 0 && (
          <Typography color="text.secondary" sx={{ p: 2 }} variant="body2">
            No {axis === "dataset" ? "datasets" : "sites"} to show.
          </Typography>
        )}
      </Box>

      {/* Bottom: selected-event detail + contour + decision + actions.
          maxHeight (not fixed height) so it grows to fit the action buttons
          (Merge / Refine) instead of pushing them below a 300px fold, but is
          capped at ~half the drawer so the event list above stays usable; its
          own scroll is the safety net at very short windows. */}
      <Divider />
      <Box
        sx={{
          flexShrink: 0,
          maxHeight: "55%",
          p: 1.5,
          overflow: "auto",
        }}
      >
        {!selected ? (
          <Typography color="text.secondary" variant="body2">
            Select an event to see details and contour controls.
          </Typography>
        ) : (
          <Stack spacing={1}>
            <Stack
              direction="row"
              alignItems="center"
              justifyContent="space-between"
            >
              <Typography variant="subtitle2">
                {selected.dtag} · event {selected.event_num}
              </Typography>
              <Stack direction="row" alignItems="center" spacing={0.5}>
                {navIndex >= 0 && (
                  <Typography variant="caption" color="text.secondary">
                    {navIndex + 1}/{eventOrder.length}
                  </Typography>
                )}
                <Tooltip title="Previous event" arrow>
                  <span>
                    <IconButton
                      size="small"
                      disabled={!prevEvent || loadingId != null}
                      onClick={() => goAdjacent(-1)}
                    >
                      <NavigateBeforeIcon fontSize="small" />
                    </IconButton>
                  </span>
                </Tooltip>
                <Tooltip title="Next event" arrow>
                  <span>
                    <IconButton
                      size="small"
                      disabled={!nextEvent || loadingId != null}
                      onClick={() => goAdjacent(+1)}
                    >
                      <NavigateNextIcon fontSize="small" />
                    </IconButton>
                  </span>
                </Tooltip>
              </Stack>
            </Stack>
            <Box
              sx={{
                display: "grid",
                gridTemplateColumns: "1fr 1fr",
                gap: 0.5,
                fontSize: 13,
              }}
            >
              <span>Quality (1 − BDC)</span>
              <strong>
                {eventQuality(selected) != null
                  ? `${Math.round(eventQuality(selected)! * 100)}%`
                  : "—"}
              </strong>
              <span>BDC</span>
              <strong>{selected.bdc ?? "—"}</strong>
              <span>Z-peak</span>
              <strong>{selected.z_peak?.toFixed(2) ?? "—"}</strong>
              <span>Event fraction</span>
              <strong>{selected.event_fraction ?? "—"}</strong>
              <span>Cluster size</span>
              <strong>{selected.cluster_size ?? "—"}</strong>
              <span>Site</span>
              <strong>{selected.site_num ?? "—"}</strong>
            </Box>

            {/* One compact row per loaded map: toggle · brief label+σ ·
                slider, all on a single line to save vertical space. Lets all
                three (event, 2Fo-Fc, Fo-Fc) be contoured + hidden to declutter. */}
            {maps.length === 0 ? (
              <Typography variant="caption" color="text.secondary">
                No maps loaded.
              </Typography>
            ) : (
              maps.map((m) => (
                <Stack
                  key={m.molNo}
                  direction="row"
                  spacing={1}
                  alignItems="center"
                >
                  <Tooltip title={m.visible ? "Hide map" : "Show map"} arrow>
                    <IconButton
                      size="small"
                      sx={{ p: 0.25 }}
                      onClick={() => onToggleVisible(m.molNo)}
                    >
                      {m.visible ? (
                        <VisibilityIcon fontSize="inherit" />
                      ) : (
                        <VisibilityOffIcon fontSize="inherit" />
                      )}
                    </IconButton>
                  </Tooltip>
                  <Typography
                    variant="caption"
                    color="text.secondary"
                    noWrap
                    sx={{ width: 96, flexShrink: 0 }}
                  >
                    {m.label} {m.sigma.toFixed(1)}σ
                  </Typography>
                  <Slider
                    size="small"
                    min={0}
                    max={m.isDifference ? 8 : 5}
                    step={0.05}
                    value={m.sigma}
                    disabled={!m.visible}
                    onChange={(_, v) =>
                      onContour(m.molNo, Array.isArray(v) ? v[0] : v)
                    }
                    sx={{ flex: 1 }}
                  />
                </Stack>
              ))
            )}

            <ToggleButtonGroup
              size="small"
              exclusive
              fullWidth
              value={selected.decision}
              onChange={(_, v) => v && setDecision(selected, v)}
            >
              <ToggleButton value="hit" color="success">
                Hit
              </ToggleButton>
              <ToggleButton value="no_hit" color="error">
                No hit
              </ToggleButton>
              <ToggleButton value="ambiguous">Ambiguous</ToggleButton>
            </ToggleButtonGroup>

            {/* BUILD/SAVE actions, sharing one row when both apply: a candidate
                pose (not yet merged) gets the Merge button alongside Save;
                otherwise Save is alone (full width). Merge = ligand-specific
                shortcut that also asserts a hit; Save = commit any Moorhen edit
                (waters, rotamers, alt-confs/occupancy…), no hit assertion. */}
            <Stack direction="row" spacing={1}>
              {eventPoseState(selected) === "candidate" && (
                <Tooltip
                  arrow
                  title={
                    "Merge this event's autobuilt ligand into the crystal " +
                    "model (and mark the event a hit)"
                  }
                >
                  <span style={{ flex: 1 }}>
                    <Button
                      size="small"
                      variant="contained"
                      color="info"
                      fullWidth
                      disabled={merging || !modelMolRef.current}
                      onClick={() => mergePose(selected)}
                      startIcon={
                        merging ? (
                          <CircularProgress size={14} />
                        ) : (
                          <BuildCircleIcon />
                        )
                      }
                    >
                      {merging ? "Merging…" : "Merge ligand"}
                    </Button>
                  </span>
                </Tooltip>
              )}
              <Tooltip
                arrow
                title={
                  "Save your current Moorhen model edits (deleted waters, " +
                  "rotamers, alt-confs…) as this crystal's current model"
                }
              >
                <span style={{ flex: 1 }}>
                  <Button
                    size="small"
                    variant="outlined"
                    color="info"
                    fullWidth
                    disabled={merging || !modelMolRef.current}
                    onClick={() => saveModel(selected)}
                  >
                    Save model edits
                  </Button>
                </span>
              </Tooltip>
            </Stack>

            {/* Refinement is CRYSTAL-scoped: it acts on the whole-crystal
                current_model vs the dataset's data, not on this single event.
                Labelled with the dtag to make that explicit. */}
            <Divider sx={{ my: 0.5 }} />
            {(() => {
              // This crystal's refine job (per-dataset, non-modal) — may be
              // running even if you navigated here from elsewhere.
              const selJob = jobsByDataset[selected.dataset];
              const running = selJob?.status === "running";
              return (
                <>
                  <Tooltip
                    arrow
                    title={
                      refineAvail && !refineAvail.available
                        ? refineAvail.reason ||
                          "Refinement environment not available"
                        : "Refine the whole-crystal model against this " +
                          "dataset's data (servalcat). Runs in the " +
                          "background — you can inspect other crystals " +
                          "meanwhile. The refined model becomes current."
                    }
                  >
                    <span>
                      <Button
                        size="small"
                        variant="outlined"
                        fullWidth
                        disabled={!refineAvail?.available || running}
                        onClick={() => refineCrystal(selected)}
                        startIcon={
                          running ? (
                            <CircularProgress size={14} />
                          ) : undefined
                        }
                      >
                        {running
                          ? "Refining…"
                          : `Refine crystal ${selected.dtag}`}
                      </Button>
                    </span>
                  </Tooltip>
                  {selJob && selJob.status !== "running" && (
                    <Typography
                      variant="caption"
                      color={
                        selJob.status === "succeeded"
                          ? "success.main"
                          : "error.main"
                      }
                    >
                      {selJob.status === "succeeded"
                        ? "Refinement complete — model updated."
                        : "Refinement failed (see server log)."}
                    </Typography>
                  )}
                </>
              );
            })()}
          </Stack>
        )}
      </Box>
    </Box>
  );
}
