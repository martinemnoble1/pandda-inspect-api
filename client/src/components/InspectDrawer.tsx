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
  Typography,
} from "@mui/material";
import ExpandMoreIcon from "@mui/icons-material/ExpandMore";
import store from "../store";
import {
  newMap,
  newMolecule,
  recentre,
  setActiveMap,
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
  const [contour, setContour] = useState(1.0);
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

          const struct = artifactOf(ev, "structure");
          if (struct) {
            const mol = newMolecule(commandCentre, store);
            await mol.loadToCootFromURL(api.artifactUrl(struct), ev.dtag);
            await mol.addRepresentation("CBs", "/*/*");
            dispatch(addMolecule(mol as any));
          }
        } else {
          // Same dataset, different event: keep the model, but drop the old
          // event map so maps don't accumulate as you step through events.
          await clearMaps();
        }

        const emap = artifactOf(ev, "event_map");
        if (emap) {
          const map = newMap(commandCentre, store);
          await map.loadToCootFromMtzURL(
            api.artifactUrl(emap),
            `${ev.dtag}-EVENT`,
            { F: "FEVENT", PHI: "PHEVENT", useWeight: false, isDifference: false }
          );
          dispatch(addMap(map as any));
          dispatch(setActiveMap(map));
          eventMapRef.current = map;
          setContour(map.contourLevel ?? 1.0);
        }

        if (ev.xyz_centroid?.length === 3) {
          recentre(
            glRef as { current: unknown },
            ev.xyz_centroid as [number, number, number]
          );
        }
        setSelected(ev);
      } finally {
        setLoadingId(null);
      }
    },
    [glRef, commandCentre, cootInitialized, dispatch, clearLoaded, clearMaps]
  );

  const onContour = useCallback((_: Event, v: number | number[]) => {
    const level = Array.isArray(v) ? v[0] : v;
    setContour(level);
    const map = eventMapRef.current;
    if (map) {
      map.contourLevel = level;
      void map.drawMapContour();
    }
  }, []);

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
      sx={{ width: 380, height: "100%", display: "flex", flexDirection: "column" }}
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
                  {g.dataset && (
                    <Stack direction="row" spacing={0.5} sx={{ mt: 0.5 }}>
                      {g.dataset.analysed_resolution != null && (
                        <Chip
                          size="small"
                          variant="outlined"
                          label={`res ${g.dataset.analysed_resolution}`}
                        />
                      )}
                      {g.dataset.r_free != null && (
                        <Chip
                          size="small"
                          variant="outlined"
                          label={`Rfree ${g.dataset.r_free.toFixed(3)}`}
                        />
                      )}
                    </Stack>
                  )}
                </Box>
              </AccordionSummary>
              <AccordionDetails>
                {/* Ligand sketch only for the dataset currently live in Moorhen */}
                {isLiveGroup && liveLigand && (
                  <Box sx={{ mb: 1, textAlign: "center" }}>
                    <MolViewer cifUrl={api.artifactUrl(liveLigand)} />
                  </Box>
                )}
                <Stack direction="row" spacing={0.5} flexWrap="wrap" useFlexGap>
                  {g.events.map((ev) => (
                    <Chip
                      key={ev.id}
                      clickable={cootInitialized}
                      disabled={!cootInitialized}
                      onClick={() => loadEvent(ev)}
                      color={
                        selected?.id === ev.id
                          ? "warning"
                          : decisionColour(ev.decision)
                      }
                      icon={
                        loadingId === ev.id ? (
                          <CircularProgress size={14} />
                        ) : undefined
                      }
                      label={
                        axis === "site"
                          ? `${ev.dtag}:${ev.event_num}`
                          : `${ev.event_num} · ${
                              ev.event_fraction ?? "—"
                            }`
                      }
                    />
                  ))}
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
