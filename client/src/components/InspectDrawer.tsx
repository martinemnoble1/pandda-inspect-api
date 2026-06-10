import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { RefObject } from "react";
import { Link as RouterLink } from "react-router-dom";
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
  Link,
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
import HomeIcon from "@mui/icons-material/Home";
import store from "../store";
import {
  autoReadMtzFromURL,
  hideMap,
  newMap,
  newMolecule,
  recentre,
  setActiveMap,
  setContourLevel,
  setMapColours,
  setRequestDrawScene,
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
  projectId: number;
  glRef: RefObject<unknown>;
  commandCentre: RefObject<moorhen.CommandCentre | null>;
  cootInitialized: boolean;
}

// PanDDA event maps are contoured in ABSOLUTE map units, not σ. They're
// BDC-corrected real-space maps whose box is mostly flat-zero outside the event
// — so the whole-box RMSD is tiny (~0.13 for BAZ2B) and σ = level/RMSD is a
// meaningless ~18, which is why a σ slider pinned at the rail. The native unit
// here IS absolute: pandda2's "Optimal Contour" and Coot's suggestedContour
// level are both absolute on this scale (~0.8–2.4 for BAZ2B). So event maps get
// an absolute slider seeded from optimal_contour (else Coot's suggestion, else
// a fallback). See contour-units memory.
const DEFAULT_EVENT_LEVEL = 2.0; // absolute fallback when no per-event hint
const EVENT_LEVEL_MAX = 6.0; // absolute slider ceiling for event maps
// View zoom on event select. Moorhen zoom is a frustum scale (default 1.0,
// smaller = closer); the 1.0 default frames the whole crystal, too far for a
// single binding pocket. ~0.35 focuses on the event without clipping its
// surroundings.
const EVENT_ZOOM = 0.35;
// Model-based maps stay in σ (their RMSD is meaningful — full-cell X-ray maps).
const DEFAULT_2FOFC_SIGMA = 1.5;
const DEFAULT_FOFC_SIGMA = 3.0;
const MAP_SIGMA_MAX = 5;
const DIFF_SIGMA_MAX = 8;

// Map colours PINNED BY ROLE (rgb 0–255), re-asserted on every event load so the
// current event always reads the same colours. This overrides Coot/Moorhen's
// hue-rotating wheel (MoorhenMap.setDefaultColour, +10°/map), which otherwise
// drifts as we churn maps across switches — the "colours kept changing between
// datasets" complaint. Event map = teal (its own identity); 2Fo-Fc = Moorhen's
// canonical blue (the familiar default). Fo-Fc keeps the fixed ±green/red
// difference default — NOT pinned here. See the map-state refactor doc, PR A2.
const EVENT_MAP_COLOUR = { r: 32, g: 178, b: 170 }; // teal (LightSeaGreen)
const MODEL_2FOFC_COLOUR = { r: 77, g: 77, b: 179 }; // Moorhen's default blue

// A map currently loaded in the viewer, with the UI state to contour + toggle
// it. ``map`` is the live MoorhenMap. The slider works in this map's native
// ``unit``: event maps in ABSOLUTE map units (their box-RMSD is tiny/unusable),
// model maps in σ (converted to absolute via RMSD when applied). ``sliderValue``
// is the value in that unit; Coot always receives absolute.
interface LoadedMap {
  map: MoorhenMapLike;
  molNo: number;
  label: string;
  unit: "sigma" | "absolute";
  sliderValue: number; // contour in this map's `unit`
  isDifference: boolean;
  visible: boolean;
}

const artifactOf = (ev: PanddaEvent, kind: string): Artifact | undefined =>
  ev.artifacts.find((a) => a.kind === kind);

const decisionColour = (d: string) =>
  d === "hit" ? "success" : d === "no_hit" ? "error" : "default";

export function InspectDrawer({
  projectName,
  projectId,
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

  // Pin a NON-difference map's colour by role (rgb 0–255) and recolour it now.
  // The setMapColours dispatch is the durable part — getMapContourParams reads
  // that slice in preference to Coot's colour wheel, so every subsequent redraw
  // (incl. the MapManager mount effect) honours it. fetchColourAndRedraw +
  // setRequestDrawScene force the recolour immediately, mirroring Moorhen's own
  // MapColourSelector. Errors are swallowed — colour is cosmetic, never fatal.
  const pinMapColour = useCallback(
    (map: MoorhenMapLike, rgb: { r: number; g: number; b: number }) => {
      dispatch(setMapColours({ molNo: map.molNo, rgb }));
      map
        .fetchColourAndRedraw()
        .then(() => dispatch(setRequestDrawScene()))
        .catch(() => {
          /* non-fatal: a difference map would reject; callers skip those */
        });
    },
    [dispatch]
  );

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
            await mol.loadToCootFromURL(api.artifactUrl(model), ev.dtag, {
              headers: api.authHeaders(),
            });
            // Load the ligand restraint dictionary so the LIG residue bonds and
            // refines correctly. Without it, Moorhen auto-fetches
            // monomers/l/LIG.cif (a 404) and draws bare atoms. Our dict is
            // embedded in the DB (data/<dtag>/ligand.cif) and served as text.
            const lig = artifactOf(ev, "ligand");
            let dictLoaded = false;
            if (lig) {
              try {
                const cif = await fetch(api.artifactUrl(lig), {
                  headers: api.authHeaders(),
                }).then((r) => (r.ok ? r.text() : ""));
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
            ev.xyz_centroid as [number, number, number],
            EVENT_ZOOM
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
              false,
              false,
              { headers: api.authHeaders() }
            );
          } else {
            await map.loadToCootFromMtzURL(
              api.artifactUrl(emap),
              `${ev.dtag}-EVENT`,
              { F: "FEVENT", PHI: "PHEVENT", useWeight: false, isDifference: false },
              { headers: api.authHeaders() }
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
          // Contour level — event maps are contoured in ABSOLUTE map units, NOT
          // σ (see contour-units memory + the constants above). The box is
          // mostly flat-zero outside the event, so the whole-box RMSD is tiny
          // (~0.13) and σ = level/RMSD blows up to a meaningless ~18 that pins
          // the slider at its rail. The native scale here is absolute, and three
          // signals agree on it: pandda2's "Optimal Contour" (~2.4, a per-event
          // threshold on raw BDC-corrected map values), Coot's own
          // suggestedContourLevel (~0.8), and the level we apply — all absolute.
          //
          // BDC correction restores bound-state density toward full occupancy,
          // so these are viewed like a normal 2Fo-Fc map (single positive
          // contour, isDifference=false), NOT an Fo-Fc difference map. Seed from
          // Coot's suggestedContourLevel — empirically the level that reads
          // "about right" for these BAZ2B event maps (~0.8 abs). pandda2's
          // optimal_contour (~2.4) is a signal-detection threshold, not a
          // viewing level: it renders too tight as a default. Fall back to
          // optimal_contour then DEFAULT_EVENT_LEVEL if no suggestion. The
          // slider (absolute, 0–EVENT_LEVEL_MAX) lets the user retune.
          const level =
            typeof map.suggestedContourLevel === "number" &&
            map.suggestedContourLevel > 0
              ? map.suggestedContourLevel
              : ev.optimal_contour != null && ev.optimal_contour > 0
              ? (ev.optimal_contour as number)
              : DEFAULT_EVENT_LEVEL;
          dispatch(addMap(map as any));
          // NB: we do NOT setActiveMap inline at addMap time — a freshly-loaded
          // CCP4 map has mapCentre=null, and making it active mounts Moorhen's
          // MapScrollWheelListener, which reads mapCentre[0] unconditionally and
          // crashes the render tree. Activation happens once, after setMaps,
          // guarded by fetchMapCentre() — see the activation block below.
          // Set the level via Redux — MoorhenMapManager re-contours off the
          // `contourLevels` slice, NOT off map.contourLevel (see shim note).
          dispatch(setContourLevel({ molNo: map.molNo, contourLevel: level }));
          // Pin the event map's colour (teal) so it's identical every event —
          // overrides Coot's drifting colour wheel (PR A2).
          pinMapColour(map, EVENT_MAP_COLOUR);
          loaded.push({
            map,
            molNo: map.molNo,
            label: "Event",
            unit: "absolute",
            sliderValue: level,
            isDifference: false,
            visible: true,
          });
        }

        // Model-based maps from current_sf (dimple MTZ at first, refined
        // servalcat MTZ after refinement): the 2mFo-DFc + mFo-DFc maps for
        // judging the CURRENT model, alongside the event map.
        //
        // Columns: HEURISTIC-FIRST. Default to Coot's auto_read_make_and_draw
        // (autoReadMtzFromURL) — it reads the MTZ header and picks the standard
        // coefficient pairs for ANY refmac-family file (dimple 2FOFCWT,
        // servalcat FWT/DELFWT, …), with isDifference detected per map. This is
        // robust to producer/version drift, which hard-coded names are NOT (and
        // PanDDA2 doesn't persist its column-override arg anywhere we can read).
        // An artifact MAY still pin explicit map_columns to OVERRIDE the
        // heuristic — the escape hatch for a pathological MTZ. Cleaned by
        // clearMaps each switch.
        const sf = ev.current_sf;
        // Stage a loaded map (contour seeded in σ → absolute via RMSD) and
        // register it with the store, sharing the path for both column sources.
        const stageModelMap = (mmap: MoorhenMapLike, isDiff: boolean) => {
          const msigma = isDiff ? DEFAULT_FOFC_SIGMA : DEFAULT_2FOFC_SIGMA;
          const mlevel =
            typeof mmap.mapRmsd === "number" && mmap.mapRmsd > 0
              ? msigma * mmap.mapRmsd
              : msigma;
          dispatch(addMap(mmap as any));
          dispatch(
            setContourLevel({ molNo: mmap.molNo, contourLevel: mlevel })
          );
          // Pin the 2Fo-Fc map to Moorhen's canonical blue. The Fo-Fc difference
          // map keeps its fixed ±green/red default — fetchColourAndRedraw rejects
          // for diff maps anyway, so only pin the non-diff one (PR A2).
          if (!isDiff) pinMapColour(mmap, MODEL_2FOFC_COLOUR);
          loaded.push({
            map: mmap,
            molNo: mmap.molNo,
            label: isDiff ? "Fo-Fc" : "2Fo-Fc",
            unit: "sigma",
            sliderValue: msigma,
            isDifference: isDiff,
            visible: true,
          });
        };
        if (sf && sf.map_columns?.length) {
          // Explicit override: caller pinned the columns. Isolate each load so
          // one bad column can't abort the whole event load (which previously
          // left an empty panel — "No maps loaded" — with the event map still
          // on screen).
          for (const col of sf.map_columns) {
            try {
              const mmap = newMap(commandCentre, store);
              await mmap.loadToCootFromMtzURL(
                api.artifactUrl(sf),
                sf.relpath,
                {
                  F: col.F,
                  PHI: col.PHI,
                  isDifference: col.isDifference,
                  useWeight: false,
                },
                { headers: api.authHeaders() }
              );
              stageModelMap(mmap, col.isDifference);
            } catch (err) {
              // eslint-disable-next-line no-console
              console.warn(
                `Map column ${col.F}/${col.PHI} failed to load from ` +
                  `${sf.relpath}; skipping.`,
                err
              );
            }
          }
        } else if (sf) {
          // Default: let Coot detect the coefficient columns.
          try {
            const autoMaps = await autoReadMtzFromURL(
              api.artifactUrl(sf),
              sf.relpath.split("/").pop() || "model.mtz",
              commandCentre,
              store,
              { headers: api.authHeaders() }
            );
            for (const mmap of autoMaps) {
              stageModelMap(mmap, !!mmap.isDifference);
            }
          } catch (err) {
            // eslint-disable-next-line no-console
            console.warn(
              `auto-read of model MTZ ${sf.relpath} failed; ` +
                `no model maps loaded.`,
              err
            );
          }
        }
        setMaps(loaded);

        // Make a map the ACTIVE map = Moorhen's CLIENT-SIDE interactive
        // refinement target (Coot's set_imol_refinement_map, via
        // MoorhenMap.setActive) AND the scroll-wheel contour target. This is NOT
        // centre-tracking — recentring is dispatch(setOrigin(...)), a separate
        // concern (see CLAUDE.md); conflating active-map with centre-follow (via
        // the MapScrollWheelListener crash below) is what previously left this
        // unset/deferred. Default to the EVENT map of the current event: the
        // BDC-corrected event map is the CORRECT target for fitting/refining a
        // PanDDA ligand into bound-state density, and scroll-to-contour then acts
        // on it too. Fall back to 2Fo-Fc only when there's no event map.
        // Refining protein geometry against 2Fo-Fc stays a manual, deliberate
        // active-map switch — not the default for the ligand-fitting task.
        //
        // GUARDED (this is why fb17bf4 dropped inline setActiveMap): activating a
        // map mounts MapScrollWheelListener, which reads map.mapCentre[0] on
        // render with no null guard. A freshly-loaded CCP4 map has mapCentre=null
        // → render-tree crash. fetchMapCentre() (Coot get_map_molecule_centre)
        // populates it first; if that fails or yields null we SKIP activation
        // rather than risk the crash — refinement just needs a manual target
        // pick in that (rare) case, no worse than before.
        const activeTarget =
          loaded.find((m) => m.label === "Event") ??
          loaded.find((m) => m.label === "2Fo-Fc");
        if (activeTarget) {
          try {
            await activeTarget.map.fetchMapCentre();
            if (activeTarget.map.mapCentre) {
              dispatch(setActiveMap(activeTarget.map));
            } else {
              // eslint-disable-next-line no-console
              console.warn(
                "Skipping setActiveMap: mapCentre unresolved for " +
                  `${activeTarget.label} map (molNo ${activeTarget.molNo}).`
              );
            }
          } catch (err) {
            // eslint-disable-next-line no-console
            console.warn(
              "fetchMapCentre failed; not setting active map (refinement " +
                "target must be picked manually).",
              err
            );
          }
        }

        // Re-assert our contour levels AFTER MoorhenMapManager mounts. Its mount
        // effect (intiliaseMap) dispatches its OWN default contourLevel for each
        // freshly-added map (non-EM non-difference → 1*mapRmsd), which races with
        // and CLOBBERS the setContourLevel we dispatched synchronously above —
        // the symptom is a map that renders at Moorhen's default until the slider
        // is first touched, then "jumps" to our intended level. A macrotask runs
        // strictly after React flushes that mount effect, so re-dispatching here
        // wins. (Levels are absolute; mirror onContour's unit handling.)
        const desired = loaded.map((m) => ({
          molNo: m.molNo,
          level:
            m.unit === "sigma" &&
            typeof m.map.mapRmsd === "number" &&
            m.map.mapRmsd > 0
              ? m.sliderValue * m.map.mapRmsd
              : m.sliderValue,
        }));
        setTimeout(() => {
          for (const d of desired) {
            dispatch(
              setContourLevel({ molNo: d.molNo, contourLevel: d.level })
            );
          }
        }, 0);

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
            `${ev.dtag}-pose-${ev.event_num}`,
            { headers: api.authHeaders() }
          );
          // Bond the LIG with its dict (same embedded CIF as the model).
          const lig = artifactOf(ev, "ligand");
          if (lig) {
            try {
              const cif = await fetch(api.artifactUrl(lig), {
                headers: api.authHeaders(),
              }).then((r) => (r.ok ? r.text() : ""));
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
      pinMapColour,
    ]
  );
  loadEventRef.current = loadEvent;

  // Contour ONE of the loaded maps (by molNo). The slider value is in that
  // map's native unit: event maps ABSOLUTE (pass straight to Coot), model maps
  // σ (multiply by RMSD — Coot always contours in absolute). Dispatch — the
  // MapManager redraws off the Redux contourLevels slice (poking
  // map.contourLevel + drawMapContour does not re-render).
  const onContour = useCallback(
    (molNo: number, value: number) => {
      setMaps((prev) =>
        prev.map((m) =>
          m.molNo === molNo ? { ...m, sliderValue: value } : m
        )
      );
      const m = maps.find((x) => x.molNo === molNo);
      const rmsd = m?.map.mapRmsd;
      const level =
        m?.unit === "sigma" && typeof rmsd === "number" && rmsd > 0
          ? value * rmsd
          : value;
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
      // The decision lives on the run-independent Finding, so it applies to
      // EVERY event sharing that finding — the same binding site seen by other
      // runs. Push the decision/review subset to those siblings too.
      const shared = (e: PanddaEvent) =>
        ev.finding != null ? e.finding === ev.finding : e.id === ev.id;
      const review = {
        decision: updated.decision,
        confidence: updated.confidence,
        comment: updated.comment,
        inspected_by: updated.inspected_by,
        inspected_at: updated.inspected_at,
      };
      const apply = (e: PanddaEvent) =>
        e.id === ev.id ? { ...e, ...updated } : { ...e, ...review };
      setDatasets((prev) =>
        prev.map((ds) => ({
          ...ds,
          events: ds.events.map((e) => (shared(e) ? apply(e) : e)),
        }))
      );
      setSelected((s) => (s && shared(s) ? apply(s) : s));
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

  // Auto-open a group so the user lands ready to click event chips, without an
  // extra click:
  //  • on first arrival (nothing expanded yet) → open the first group;
  //  • whenever the visible set narrows to exactly ONE group (e.g. a filter or
  //    search that uniquely identifies a dataset) → open that one.
  // Keyed on the group signature so it fires when the LIST changes, not on every
  // render — so a deliberate collapse isn't immediately undone (the signature is
  // unchanged, so we don't re-open). The selected-event effect below still wins
  // when the user is actively navigating events.
  const autoOpenSig = useRef<string | null>(null);
  useEffect(() => {
    if (groups.length === 0) return;
    const sig = groups.map((g) => g.key).join("|");
    // Only act when the LIST itself changed (first arrival, or a filter/search
    // that produced a different set) — not on every render, so a deliberate
    // collapse isn't undone. Guard + record synchronously (NOT inside the
    // setState updater) so Strict Mode's double-invoke can't defeat it.
    if (autoOpenSig.current === sig) return;
    autoOpenSig.current = sig;
    const single = groups.length === 1;
    // Open the sole group, or the first group when nothing is open yet.
    setExpanded((cur) =>
      single || cur === false ? groups[0].key : cur
    );
  }, [groups]);

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
        : axis === "finding"
        ? `${selected.dtag}-${
            selected.finding != null
              ? `f-${selected.finding}`
              : `u-${selected.event_num}`
          }`
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
          {/* Breadcrumb: Home (top of app) · project name (project summary).
              Lets the user navigate back out of the full-bleed inspect view. */}
          <Stack direction="row" spacing={0.5} alignItems="center">
            <Tooltip title="Home">
              <IconButton
                component={RouterLink}
                to="/"
                size="small"
                edge="start"
              >
                <HomeIcon fontSize="small" />
              </IconButton>
            </Tooltip>
            <Link
              component={RouterLink}
              to={`/projects/${projectId}`}
              variant="subtitle1"
              underline="hover"
              color="inherit"
            >
              {projectName}
            </Link>
          </Stack>
          <ToggleButtonGroup
            size="small"
            exclusive
            value={axis}
            onChange={(_, v) => v && setAxis(v)}
          >
            <ToggleButton value="dataset">Dataset</ToggleButton>
            <ToggleButton value="site">Site</ToggleButton>
            <ToggleButton value="finding">Finding</ToggleButton>
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
              axis === "dataset"
                ? "Filter datasets…"
                : axis === "finding"
                ? "Filter findings…"
                : "Filter sites…"
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
            axis === "dataset"
              ? selected?.dtag === g.key
              : axis === "finding"
              ? g.events.some((e) => e.id === selected?.id)
              : false;
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
                    <MolViewer
                      cifUrl={api.artifactUrl(liveLigand)}
                      fetchInit={{ headers: api.authHeaders() }}
                    />
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
                    : axis === "finding"
                    ? "run · RSCC (same site, each run's build)"
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
                    const runLabel =
                      ev.run_group ??
                      (ev.run_id != null ? `run ${ev.run_id}` : "run ?");
                    const label =
                      axis === "site"
                        ? `${ev.dtag}:${ev.event_num}`
                        : axis === "finding"
                        ? `${runLabel} · ${
                            ev.rscc != null
                              ? `RSCC ${ev.rscc.toFixed(2)}`
                              : `ev${ev.event_num}`
                          }`
                        : `Event ${ev.event_num} · ${qStr}`;
                    const tip = (
                      <Box sx={{ fontSize: 12, lineHeight: 1.5 }}>
                        <div>
                          <strong>
                            {ev.dtag} · event {ev.event_num}
                          </strong>
                        </div>
                        {ev.run_group != null && (
                          <div>Run: {ev.run_group}</div>
                        )}
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
            No{" "}
            {axis === "dataset"
              ? "datasets"
              : axis === "finding"
              ? "findings"
              : "sites"}{" "}
            to show.
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
                    {m.label} {m.sliderValue.toFixed(2)}
                    {m.unit === "sigma" ? "σ" : ""}
                  </Typography>
                  <Slider
                    size="small"
                    min={0}
                    max={
                      m.unit === "absolute"
                        ? EVENT_LEVEL_MAX
                        : m.isDifference
                        ? DIFF_SIGMA_MAX
                        : MAP_SIGMA_MAX
                    }
                    step={0.05}
                    value={m.sliderValue}
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
