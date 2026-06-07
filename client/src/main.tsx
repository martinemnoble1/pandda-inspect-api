import React from "react";
import ReactDOM from "react-dom/client";
import { Provider } from "react-redux";
import { createBrowserRouter, RouterProvider } from "react-router-dom";
import { CssBaseline } from "@mui/material";
import store from "./store";
import { Layout } from "./components/Layout";
import { Landing } from "./pages/Landing";
import { ImportPage } from "./pages/ImportPage";
import { ProjectBrowser } from "./pages/ProjectBrowser";
import { ProjectDashboard } from "./pages/ProjectDashboard";
import { InspectPage } from "./pages/InspectPage";
import { SettingsPage } from "./pages/SettingsPage";
import { RunStatus } from "./pages/RunStatus";
import { RunsPage } from "./pages/RunsPage";
import { authEnabled, ensureSignedIn, startTokenRefresh } from "./auth";

const router = createBrowserRouter(
  [
    {
      element: <Layout />,
      children: [
        { path: "/", element: <Landing /> },
        { path: "/import", element: <ImportPage /> },
        { path: "/projects", element: <ProjectBrowser /> },
        { path: "/projects/:projectId", element: <ProjectDashboard /> },
        // Global runs list (re-discovery / overview).
        { path: "/runs", element: <RunsPage /> },
        // Landing page for a triggered run — the API's ui_url points here.
        { path: "/runs/:runId", element: <RunStatus /> },
        { path: "/settings", element: <SettingsPage /> },
      ],
    },
    // Inspect is full-bleed (no chrome) so Moorhen fills the viewport.
    { path: "/projects/:projectId/inspect", element: <InspectPage /> },
  ],
  // Honour the public path prefix when path-mounted (VITE_BASE=/reinspect);
  // BASE_URL is "/" by default, so this is a no-op for the desktop build.
  { basename: import.meta.env.BASE_URL }
);

function render() {
  ReactDOM.createRoot(document.getElementById("root")!).render(
    <React.StrictMode>
      <Provider store={store}>
        <CssBaseline />
        <RouterProvider router={router} />
      </Provider>
    </React.StrictMode>
  );
}

// Cloud build (VITE_AAD_* set): complete AAD sign-in before rendering, so the
// first data fetch already carries a bearer. ensureSignedIn redirects to the
// login when there's no account (this page load then ends). Desktop/dev:
// authEnabled is false → this resolves immediately and nothing auth runs.
if (authEnabled) {
  ensureSignedIn()
    .then(() => {
      startTokenRefresh();
      render();
    })
    .catch((e) => {
      console.error("AAD sign-in failed", e);
      render(); // render anyway; API calls will surface the 401
    });
} else {
  render();
}
