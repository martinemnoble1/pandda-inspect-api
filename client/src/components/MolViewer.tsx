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
}: {
  cifUrl: string;
  width?: number;
  height?: number;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setError(false);
    (async () => {
      try {
        const cif = await fetch(cifUrl).then((r) => r.text());
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
  }, [cifUrl, width, height]);

  if (error)
    return (
      <Typography variant="caption" color="text.secondary">
        (ligand sketch unavailable)
      </Typography>
    );
  return <Box ref={ref} sx={{ "& svg": { maxWidth: "100%" } }} />;
}
