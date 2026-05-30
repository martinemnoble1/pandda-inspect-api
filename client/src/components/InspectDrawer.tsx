import { useCallback, useEffect, useRef, useState } from "react";
import type { RefObject } from "react";
import { useDispatch } from "react-redux";
import { addMap, addMolecule, removeMap, removeMolecule } from "moorhen";
import type { moorhen } from "moorhen/types/moorhen";
import {
  Box,
  Chip,
  CircularProgress,
  Divider,
  List,
  ListItemButton,
  ListItemText,
  Slider,
  Stack,
  TextField,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from "@mui/material";
import store from "../store";
import {
  newMap,
  newMolecule,
  recentre,
  setActiveMap,
  type MoorhenMapLike,
} from "../moorhen-shim";
import { api, type Artifact, type PanddaEvent } from "../api";

interface Props {
  projectName: string;
  glRef: RefObject<unknown>;
  commandCentre: RefObject<moorhen.CommandCentre | null>;
  cootInitialized: boolean;
}

const artifactOf = (ev: PanddaEvent, kind: string): Artifact | undefined =>
  ev.artifacts.find((a) => a.kind === kind);

export function InspectDrawer({
  projectName,
  glRef,
  commandCentre,
  cootInitialized,
}: Props) {
  const dispatch = useDispatch();
  const [events, setEvents] = useState<PanddaEvent[]>([]);
  const [search, setSearch] = useState("");
  const [loadingId, setLoadingId] = useState<number | null>(null);
  const [selected, setSelected] = useState<PanddaEvent | null>(null);
  const [contour, setContour] = useState(1.0);
  const loadedDtag = useRef<string | null>(null);
  const eventMapRef = useRef<MoorhenMapLike | null>(null);

  useEffect(() => {
    if (!projectName) return;
    api
      .listEvents(projectName)
      .then((d) => setEvents(d.results))
      .catch(() => setEvents([]));
  }, [projectName]);

  const clearLoaded = useCallback(async () => {
    const molecules: any[] =
      (store.getState() as any).molecules.moleculeList ?? [];
    for (const m of molecules) {
      await m.delete();
      dispatch(removeMolecule(m));
    }
    const maps: any[] = (store.getState() as any).maps ?? [];
    for (const mp of maps) {
      await mp.delete();
      dispatch(removeMap(mp));
    }
    eventMapRef.current = null;
  }, [dispatch]);

  const loadEvent = useCallback(
    async (ev: PanddaEvent) => {
      // Coot must be fully initialised: commandCentre.current exists early but
      // its cootCommand isn't wired until cootInitialized flips true.
      const cc = commandCentre.current as
        | (moorhen.CommandCentre & { cootCommand?: unknown })
        | null;
      if (!cootInitialized || !glRef.current || !cc?.cootCommand) return;
      setLoadingId(ev.id);
      try {
        if (loadedDtag.current !== ev.dtag) {
          await clearLoaded();
          loadedDtag.current = ev.dtag;

          const struct = artifactOf(ev, "structure");
          if (struct) {
            // Pass the ref, not .current — Moorhen reads commandCentre.current
            // internally.
            const mol = newMolecule(commandCentre, store);
            await mol.loadToCootFromURL(api.artifactUrl(struct), ev.dtag);
            // Representation before dispatch: 0.23's sequence viewer reads
            // representations[0] for any molecule with a sequence.
            await mol.addRepresentation("CBs", "/*/*");
            dispatch(addMolecule(mol as any));
          }
        }

        const emap = artifactOf(ev, "event_map");
        if (emap) {
          const map = newMap(commandCentre, store);
          await map.loadToCootFromMtzURL(api.artifactUrl(emap), `${ev.dtag}-EVENT`, {
            F: "FEVENT",
            PHI: "PHEVENT",
            useWeight: false,
            isDifference: false,
          });
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
    [glRef, commandCentre, cootInitialized, dispatch, clearLoaded]
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

  const setDecision = useCallback(async (ev: PanddaEvent, decision: string) => {
    const updated = await api.setDecision(ev.id, { decision });
    setEvents((prev) =>
      prev.map((e) => (e.id === ev.id ? { ...e, ...updated } : e))
    );
    setSelected((s) => (s && s.id === ev.id ? { ...s, ...updated } : s));
  }, []);

  const filtered = events.filter(
    (e) =>
      search.trim() === "" ||
      e.dtag.toLowerCase().includes(search.toLowerCase())
  );

  return (
    <Box
      sx={{
        width: 360,
        height: "100%",
        display: "flex",
        flexDirection: "column",
      }}
    >
      {/* Top: searchable event list */}
      <Box sx={{ p: 1, flexShrink: 0 }}>
        <Typography variant="subtitle1">Events — {projectName}</Typography>
        {!cootInitialized && (
          <Typography variant="caption" color="text.secondary">
            Waiting for Moorhen to finish loading…
          </Typography>
        )}
        <TextField
          size="small"
          fullWidth
          placeholder="Filter by dtag…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          sx={{ mt: 1 }}
        />
      </Box>
      <Box sx={{ flex: 1, overflow: "auto", minHeight: 0 }}>
        <List dense disablePadding>
          {filtered.map((ev) => (
            <ListItemButton
              key={ev.id}
              divider
              disabled={!cootInitialized}
              selected={selected?.id === ev.id}
              onClick={() => loadEvent(ev)}
            >
              <ListItemText
                primary={`${ev.dtag} · event ${ev.event_num}`}
                secondary={`BDC ${ev.bdc ?? "—"} · Z ${
                  ev.z_peak?.toFixed(1) ?? "—"
                }`}
              />
              {loadingId === ev.id && <CircularProgress size={16} />}
              {ev.decision !== "unreviewed" && (
                <Chip
                  size="small"
                  label={ev.decision}
                  color={
                    ev.decision === "hit"
                      ? "success"
                      : ev.decision === "no_hit"
                      ? "error"
                      : "default"
                  }
                />
              )}
            </ListItemButton>
          ))}
        </List>
      </Box>

      {/* Bottom: fixed 300px detail + contour panel for the selected event */}
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
