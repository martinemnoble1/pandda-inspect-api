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
  Chip,
  CircularProgress,
  Divider,
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
import store from "../store";
import {
  newMap,
  newMolecule,
  recentre,
  setActiveMap,
  setContourLevel,
  type MoorhenMapLike,
} from "../moorhen-shim";
import { api, type Artifact, type Dataset, type PanddaEvent } from "../api";
import { groupEvents, summarise, type GroupAxis } from "../grouping";
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
  const [search, setSearch] = useState("");
  const [hitsOnly, setHitsOnly] = useState(true);
  const [expanded, setExpanded] = useState<string | false>(false);
  const [loadingId, setLoadingId] = useState<number | null>(null);
  const [selected, setSelected] = useState<PanddaEvent | null>(null);
  const [contour, setContour] = useState(DEFAULT_EVENT_SIGMA);
  const loadedDtag = useRef<string | null>(null);
  const eventMapRef = useRef<MoorhenMapLike | null>(null);

  useEffect(() => {
    if (!projectName) return;
    api
      .listDatasets(projectName)
      .then((d) => setDatasets(d.results))
      .catch(() => setDatasets([]));
  }, [projectName]);

  // Delete every map currently in the store (state.maps is an array in 0.23).
  const clearMaps = useCallback(async () => {
    const maps: any[] = (store.getState() as any).maps ?? [];
    for (const mp of maps) {
      await mp.delete();
      dispatch(removeMap(mp));
    }
    eventMapRef.current = null;
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

          // Prefer the current best model (the built/refined coords, which
          // carry the ligand) over the apo input structure. current_model is
          // the dataset's analysis/autobuild model (or a human build) when one
          // exists; otherwise fall back to the imported "structure" artifact.
          const model = ev.current_model ?? artifactOf(ev, "structure");
          if (model) {
            const mol = newMolecule(commandCentre, store);
            await mol.loadToCootFromURL(api.artifactUrl(model), ev.dtag);
            await mol.addRepresentation("CBs", "/*/*");
            dispatch(addMolecule(mol as any));
          }
        } else {
          // Same dataset, different event: keep the model, but drop the old
          // event map so maps don't accumulate as you step through events.
          await clearMaps();
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
          const sigma = DEFAULT_EVENT_SIGMA;
          const level =
            typeof map.mapRmsd === "number" && map.mapRmsd > 0
              ? sigma * map.mapRmsd
              : map.contourLevel ?? 1.0;
          dispatch(addMap(map as any));
          dispatch(setActiveMap(map));
          // Set the level via Redux — MoorhenMapManager re-contours off the
          // `contourLevels` slice, NOT off map.contourLevel (see shim note).
          dispatch(setContourLevel({ molNo: map.molNo, contourLevel: level }));
          eventMapRef.current = map;
          // Surface the level in σ for the slider (which is labelled in σ).
          setContour(sigma);
        }
        setSelected(ev);
      } finally {
        setLoadingId(null);
      }
    },
    [glRef, commandCentre, cootInitialized, dispatch, clearLoaded, clearMaps]
  );

  const onContour = useCallback(
    (_: Event, v: number | number[]) => {
      // Slider is in σ; Coot contours in ABSOLUTE units, so multiply by RMSD.
      const sigma = Array.isArray(v) ? v[0] : v;
      setContour(sigma);
      const map = eventMapRef.current;
      if (map) {
        const level =
          typeof map.mapRmsd === "number" && map.mapRmsd > 0
            ? sigma * map.mapRmsd
            : sigma;
        // Dispatch — the MapManager redraws off the Redux contourLevels slice.
        // Poking map.contourLevel + drawMapContour() does not re-render.
        dispatch(setContourLevel({ molNo: map.molNo, contourLevel: level }));
      }
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
    const withEvents = hitsOnly
      ? datasets.filter((d) => d.events.length > 0)
      : datasets;
    const grouped = groupEvents(withEvents, axis);
    const q = search.trim().toLowerCase();
    if (!q) return grouped;
    return grouped.filter(
      (g) =>
        g.title.toLowerCase().includes(q) ||
        g.subtitle?.toLowerCase().includes(q)
    );
  }, [datasets, axis, hitsOnly, search]);

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
        <TextField
          size="small"
          fullWidth
          placeholder={axis === "dataset" ? "Filter datasets…" : "Filter sites…"}
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          sx={{ mt: 1 }}
        />
        <Box sx={{ mt: 0.5 }}>
          <Chip
            size="small"
            label={hitsOnly ? "With events only" : "All datasets"}
            onClick={() => setHitsOnly((v) => !v)}
            variant={hitsOnly ? "filled" : "outlined"}
            color={hitsOnly ? "primary" : "default"}
          />
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
                      const nHits = g.events.filter(
                        (e) => e.decision === "hit"
                      ).length;
                      const topFrac = g.events.reduce<number | null>(
                        (m, e) =>
                          e.event_fraction != null
                            ? Math.max(m ?? 0, e.event_fraction)
                            : m,
                        null
                      );
                      return (
                        <>
                          {nHits > 0 && (
                            <Chip
                              size="small"
                              color="success"
                              label={`${nHits} hit${nHits === 1 ? "" : "s"}`}
                            />
                          )}
                          {topFrac != null && (
                            <Tooltip
                              title="Highest event fraction in this dataset — a quick measure of the strongest event"
                              arrow
                            >
                              <Chip
                                size="small"
                                variant="outlined"
                                color={topFrac >= 0.4 ? "primary" : "default"}
                                label={`top ${Math.round(topFrac * 100)}%`}
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
                    : "event · event-fraction"}
                </Typography>
                <Stack direction="row" spacing={0.75} flexWrap="wrap" useFlexGap>
                  {g.events.map((ev) => {
                    const isLive = selected?.id === ev.id;
                    const occ =
                      ev.event_fraction != null
                        ? `${Math.round(ev.event_fraction * 100)}%`
                        : "—";
                    const label =
                      axis === "site"
                        ? `${ev.dtag}:${ev.event_num}`
                        : `Event ${ev.event_num} · ${occ}`;
                    const tip = (
                      <Box sx={{ fontSize: 12, lineHeight: 1.5 }}>
                        <div>
                          <strong>
                            {ev.dtag} · event {ev.event_num}
                          </strong>
                        </div>
                        <div>Event fraction: {occ}</div>
                        <div>Z-peak: {ev.z_peak?.toFixed(1) ?? "—"}</div>
                        <div>BDC: {ev.bdc ?? "—"}</div>
                        <div>Cluster size: {ev.cluster_size ?? "—"}</div>
                        <div>Site: {ev.site_num ?? "—"}</div>
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
                              ) : (
                                <ViewInArIcon />
                              )
                            }
                            label={label}
                            sx={{
                              fontWeight: isLive ? 700 : 500,
                              transition: "transform 80ms ease",
                              "&:hover": { transform: "translateY(-1px)" },
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

      {/* Bottom: selected-event detail + contour + decision */}
      <Divider />
      <Box sx={{ height: 300, flexShrink: 0, p: 1.5, overflow: "auto" }}>
        {!selected ? (
          <Typography color="text.secondary" variant="body2">
            Select an event to see details and contour controls.
          </Typography>
        ) : (
          <Stack spacing={1}>
            <Typography variant="subtitle2">
              {selected.dtag} · event {selected.event_num}
            </Typography>
            <Box
              sx={{
                display: "grid",
                gridTemplateColumns: "1fr 1fr",
                gap: 0.5,
                fontSize: 13,
              }}
            >
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

            <Box>
              <Typography variant="caption" color="text.secondary">
                Event map contour: {contour.toFixed(2)} σ
              </Typography>
              <Slider
                size="small"
                min={0}
                max={5}
                step={0.05}
                value={contour}
                onChange={onContour}
                disabled={!eventMapRef.current}
              />
            </Box>

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
          </Stack>
        )}
      </Box>
    </Box>
  );
}
