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
import store, { resetMoorhenStore } from "../store";
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
            projectId={id}
            glRef={glRef}
            commandCentre={commandCentre}
            cootInitialized={!!cootInitialized}
          />
        ),
      },
    }),
    [project?.name, id, cootInitialized]
  );

  useEffect(() => {
    if (cootInitialized) dispatch(setShownSidePanel(PANEL_ID));
  }, [cootInitialized, dispatch]);

  // TEARDOWN on leaving the Moorhen page (the dashboard→moorhen→dashboard→
  // moorhen crash). MoorhenInstanceProvider's own unmount cleanup terminates the
  // CootWorker, but it leaves the APP-LEVEL store (a module singleton, shared
  // with MoorhenContainer) fully populated — stale init/ready flags
  // (cootInitialized, isGlobalInstanceReady, userPreferencesMounted) plus the
  // previous session's maps/molecules bound to the now-dead worker. The store is
  // never re-created, so its boot-time initial state never re-applies; the next
  // mount renders the menu/managers against that stale state before the fresh
  // instance has run startInstance, and crashes. Reset EVERY slice to initial so
  // the next entry replays a clean boot (preferences reload from localForage on
  // remount). One full reset instead of patching stale flags one at a time —
  // see store.ts resetMoorhenStore. Runs after the children (incl. Moorhen) have
  // already unmounted/unsubscribed, so no live subscriber sees the reset.
  useEffect(() => {
    return () => {
      dispatch(resetMoorhenStore());
    };
  }, [dispatch]);

  const collectedProps = {
    glRef,
    commandCentre,
    moleculesRef,
    mapsRef,
    activeMapRef,
    lastHoveredAtomRef,
    extraSidePanels,
    store,
    // Only override Moorhen's asset paths UNDER A PATH MOUNT. Moorhen builds
    // its worker from `${urlPrefix}/wasm/CootWorker.js`; its default urlPrefix
    // mis-resolves under /reinspect, so pin the ORIGIN-ROOTED /MoorhenAssets —
    // it bypasses the mount and hits the host's (Materia's) CORP-correct
    // /MoorhenAssets static. Monomers go to the canonical GitHub library (the
    // host serves the JS/wasm but not the monomer .cifs). Desktop (BASE_URL
    // "/") keeps Moorhen's defaults untouched, so the desktop viewer is
    // byte-identical. See docs/CLOUD_DEPLOYMENT.md (Ingress / Moorhen assets).
    ...(import.meta.env.BASE_URL !== "/"
      ? {
          urlPrefix: "/MoorhenAssets",
          monomerLibraryPath:
            "https://raw.githubusercontent.com/" +
            "MonomerLibrary/monomers/master/",
        }
      : {}),
  };

  return (
    <MoorhenInstanceProvider>
      <div style={{ position: "absolute", inset: 0 }}>
        <MoorhenContainer {...collectedProps} />
      </div>
    </MoorhenInstanceProvider>
  );
}
