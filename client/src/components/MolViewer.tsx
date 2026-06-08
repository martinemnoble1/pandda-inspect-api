import { useEffect, useRef, useState } from "react";
import { Box, Typography } from "@mui/material";

declare global {
  interface Window {
    RDKit?: {
      get_mol: (s: string) => {
        get_svg: (w: number, h: number) => string;
        delete: () => void;
      };
    };
  }
}

/**
 * 2D ligand depiction via RDKit (loaded on the main thread in index.html).
 * Fetches the ligand CIF from its artifact URL and renders an SVG sketch.
 * Ported from the prototype's MolViewer; kept deliberately small.
 */
export function MolViewer({
  cifUrl,
  width = 200,
  height = 150,
  // Forwarded to the CIF fetch so the bytes load header-authenticated under
  // AAD (e.g. { headers: api.authHeaders() }); omitted on desktop/no-auth.
  fetchInit,
}: {
  cifUrl: string;
  width?: number;
  height?: number;
  fetchInit?: RequestInit;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setError(false);
    (async () => {
      try {
        const cif = await fetch(cifUrl, fetchInit).then((r) => r.text());
        if (cancelled || !window.RDKit) return;
        const mol = window.RDKit.get_mol(cif);
        const svg = mol.get_svg(width, height);
        mol.delete();
        if (ref.current && !cancelled) ref.current.innerHTML = svg;
      } catch {
        if (!cancelled) setError(true);
      }
    })();
    return () => {
      cancelled = true;
    };
    // fetchInit is a fresh object each render (inline headers); cifUrl already
    // keys the fetch and the token is stable across one URL, so re-running on
    // fetchInit identity would needlessly refetch. Intentionally omitted.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cifUrl, width, height]);

  if (error)
    return (
      <Typography variant="caption" color="text.secondary">
        (ligand sketch unavailable)
      </Typography>
    );
  return <Box ref={ref} sx={{ "& svg": { maxWidth: "100%" } }} />;
}
