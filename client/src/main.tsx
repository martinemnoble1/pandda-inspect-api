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

const router = createBrowserRouter([
  {
    element: <Layout />,
    children: [
      { path: "/", element: <Landing /> },
      { path: "/import", element: <ImportPage /> },
      { path: "/projects", element: <ProjectBrowser /> },
      { path: "/projects/:projectId", element: <ProjectDashboard /> },
    ],
  },
  // Inspect is full-bleed (no chrome) so Moorhen fills the viewport.
  { path: "/projects/:projectId/inspect", element: <InspectPage /> },
]);

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <Provider store={store}>
      <CssBaseline />
      <RouterProvider router={router} />
    </Provider>
  </React.StrictMode>
);
