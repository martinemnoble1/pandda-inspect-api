import { useEffect, useMemo, useRef, useState } from "react";
import type { RefObject } from "react";
import { useParams } from "react-router-dom";
import { useDispatch, useSelector } from "react-redux";
import {
  MoorhenContainer,
  MoorhenInstanceProvider,
  setShownSidePanel,
} from "moorhen";
import type { MoorhenPanel } from "moorhen";
import type { webGL } from "moorhen/types/mgWebGL";
import type { moorhen } from "moorhen/types/moorhen";
import store from "../store";
import { api, type Project } from "../api";
import { InspectDrawer } from "../components/InspectDrawer";

const PANEL_ID = "panddaInspect";

/**
 * Full-bleed Moorhen with a PanDDA-inspect side panel. The pattern is ported
 * from the prototype, but ALL data comes from the REST contract (api.ts) — no
 * panddaPrefix, no results.json. Moorhen 0.23 auto-sizes to its container.
 */
export function InspectPage() {
  const { projectId } = useParams();
  const id = Number(projectId);
  const dispatch = useDispatch();
  const [project, setProject] = useState<Project | null>(null);

  const cootInitialized = useSelector(
    (s: any) => s.generalStates.cootInitialized
  );

  const glRef = useRef<webGL.MGWebGL | null>(null);
  const commandCentre = useRef<moorhen.CommandCentre | null>(null);
  const moleculesRef = useRef<moorhen.Molecule[] | null>(null);
  const mapsRef = useRef<moorhen.Map[] | null>(null);
  const activeMapRef = useRef<moorhen.Map>(
    null as unknown as moorhen.Map
  );
  const lastHoveredAtomRef = useRef<moorhen.HoveredAtom | null>(null);

  useEffect(() => {
    api.getProject(id).then(setProject).catch(() => setProject(null));
  }, [id]);

  const extraSidePanels: Record<string, MoorhenPanel> = useMemo(
    () => ({
      [PANEL_ID]: {
        icon: "MatSymFactCheck",
        label: "PanDDA inspect",
        panelContent: (
          <InspectDrawer
            projectName={project?.name ?? ""}
            glRef={glRef}
            commandCentre={commandCentre}
            cootInitialized={!!cootInitialized}
          />
        ),
      },
    }),
    [project?.name, cootInitialized]
  );

  useEffect(() => {
    if (cootInitialized) dispatch(setShownSidePanel(PANEL_ID));
  }, [cootInitialized, dispatch]);

  const collectedProps = {
    glRef,
    commandCentre,
    moleculesRef,
    mapsRef,
    activeMapRef,
    lastHoveredAtomRef,
    extraSidePanels,
    store,
  };

  return (
    <MoorhenInstanceProvider>
      <div style={{ position: "absolute", inset: 0 }}>
        <MoorhenContainer {...collectedProps} />
      </div>
    </MoorhenInstanceProvider>
  );
}
