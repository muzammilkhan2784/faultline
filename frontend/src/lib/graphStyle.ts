import type cytoscape from "cytoscape";

/** Cytoscape cannot read CSS variables, so resolve the current theme's colours. */
export interface GraphTheme {
  ink: string;
  soft: string;
  faint: string;
  line: string;
  bg: string;
  surface: string;
  accent: string;
  ok: string;
  info: string;
  wave1: string;
  wave2: string;
  wave3: string;
}

export function readTheme(): GraphTheme {
  const styles = getComputedStyle(document.documentElement);
  const rgb = (name: string): string => `rgb(${styles.getPropertyValue(`--${name}`).trim().split(/\s+/).join(",")})`;
  return {
    ink: rgb("ink"),
    soft: rgb("soft"),
    faint: rgb("faint"),
    line: rgb("line"),
    bg: rgb("bg"),
    surface: rgb("surface"),
    accent: rgb("accent"),
    ok: rgb("ok"),
    info: rgb("info"),
    wave1: rgb("wave1"),
    wave2: rgb("wave2"),
    wave3: rgb("wave3"),
  };
}

/**
 * The visual grammar of the graph:
 *
 * - **size**        scales with cyclomatic complexity
 * - **diamond**     a test
 * - **blue ring**   an entrypoint (something outside the repo calls it)
 * - **orange**      the function you are asking about
 * - **ripple**      with the impact overlay on, nodes are coloured by how many
 *                   hops they are from the changed function
 * - **edge style**  solid = exact, dashed = inferred, dotted = ambiguous
 * - **edge fade**   opacity follows confidence
 */
export function buildStylesheet(t: GraphTheme): cytoscape.StylesheetStyle[] {
  return [
    {
      selector: "node",
      style: {
        label: "data(label)",
        "font-family": '"JetBrains Mono Variable", ui-monospace, monospace',
        "font-size": 10,
        color: t.soft,
        "text-valign": "bottom",
        "text-margin-y": 5,
        "text-outline-color": t.bg,
        "text-outline-width": 3,
        "min-zoomed-font-size": 9,
        width: "mapData(complexity, 1, 30, 22, 52)",
        height: "mapData(complexity, 1, 30, 22, 52)",
        "background-color": t.surface,
        "border-width": 2,
        "border-color": t.faint,
        shape: "ellipse",
        "transition-property": "background-color, border-color, width, height",
        "transition-duration": 150,
      },
    },
    { selector: "node.nolabel", style: { "text-opacity": 0, "text-outline-opacity": 0 } },
    { selector: "node[?is_test]", style: { shape: "diamond", "border-color": t.ok, width: 30, height: 30 } },
    { selector: "node[?is_entrypoint]", style: { "border-color": t.info, "border-width": 3 } },
    { selector: "node.hover", style: { "border-color": t.ink, color: t.ink } },
    {
      selector: "node.selected",
      style: {
        "background-color": t.accent,
        "border-color": t.accent,
        "border-width": 4,
        color: t.ink,
        "font-weight": 700,
        "font-size": 12,
        "min-zoomed-font-size": 0,
        "z-index": 20,
        "overlay-color": t.accent,
        "overlay-opacity": 0.12,
        "overlay-padding": 12,
      },
    },
    { selector: "node.nolabel.selected", style: { "text-opacity": 1, "text-outline-opacity": 1 } },
    // The shockwave: nearer to the change = hotter.
    { selector: "node[wave = 1]", style: { "background-color": t.wave1, "border-color": t.wave1, color: t.ink } },
    { selector: "node[wave = 2]", style: { "background-color": t.wave2, "border-color": t.wave2 } },
    { selector: "node[wave >= 3]", style: { "background-color": t.wave3, "border-color": t.wave3 } },
    {
      selector: "edge",
      style: {
        width: 1.5,
        "curve-style": "bezier",
        "line-color": t.faint,
        "target-arrow-color": t.faint,
        "target-arrow-shape": "triangle",
        "arrow-scale": 0.9,
        opacity: "mapData(confidence, 0.3, 1, 0.3, 0.95)",
      },
    },
    { selector: 'edge[resolution = "inferred"]', style: { "line-style": "dashed", "line-dash-pattern": [6, 4] } },
    { selector: 'edge[resolution = "ambiguous"]', style: { "line-style": "dotted", width: 2 } },
    {
      selector: "edge.hover",
      style: { "line-color": t.accent, "target-arrow-color": t.accent, opacity: 1, width: 2.5 },
    },
    { selector: ".faded", style: { opacity: 0.12 } },
  ] as cytoscape.StylesheetStyle[];
}
