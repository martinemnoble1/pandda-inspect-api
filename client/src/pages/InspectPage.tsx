import { useEffect, useMemo, useRef, useState } from "react";
import type { RefObject } from "react";
import { useParams, useSearchParams } from "react-router-dom";
import { useDispatch, useSelector } from "react-redux";
import {
  MoorhenContainer,
  MoorhenInstanceProvider,
  MoorhenMenuSystem,
  setShownSidePanel,
} from "moorhen/react-lib";
import type { MoorhenPanel } from "moorhen/react-lib";
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
  // Deep-link: `?site=N` (from clicking a site bar on the dashboard) tells the
  // drawer to open in Site mode focused on that site.
  const [searchParams] = useSearchParams();
  const siteParam = searchParams.get("site");
  const initialSite = siteParam != null ? Number(siteParam) : null;

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
            initialSite={initialSite}
          />
        ),
      },
    }),
    [project?.name, id, cootInitialized, initialSite]
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

  // Moorhen 1.0 moved per-instance state into a React context, and
  // MoorhenInstanceProvider now REQUIRES a menuSystem (it builds
  // `new MoorhenInstance(ref, menuSystem)` from it). One per provider; memoise
  // so it isn't rebuilt each render. We manage our own store (store.ts), so we
  // wire the provider directly rather than via MoorhenProvider (which would
  // create a second, un-memoised store). See migration guide §2.
  const menuSystem = useMemo(() => new MoorhenMenuSystem(), []);

  return (
    <MoorhenInstanceProvider menuSystem={menuSystem}>
      <div style={{ position: "absolute", inset: 0 }}>
        <MoorhenContainer {...collectedProps} />
      </div>
    </MoorhenInstanceProvider>
  );
}
