import cytoscape, { type Core, type ElementDefinition, type EventObject } from "cytoscape";
import dagre from "cytoscape-dagre";
import { Maximize2, RotateCcw } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import type { GraphResponse } from "@/api/types";
import { formatConfidence, shortFqn } from "@/lib/format";
import { buildStylesheet, readTheme } from "@/lib/graphStyle";

type LayoutMode = "auto" | "flow" | "rings";

/** Above this many nodes a left-to-right hierarchy becomes a single unreadable column. */
const RINGS_THRESHOLD = 24;

/** Below this zoom level node labels are hidden (except the selected node's). */
const LABEL_MIN_ZOOM = 0.8;

const FIT_PADDING = 40;

/**
 * Two layouts. "Flow" is a left-to-right hierarchy (callers left, callees right) and
 * is best for small graphs. "Rings" places nodes on concentric circles by hop
 * distance from the selected function -- the ripple from the epicentre -- and
 * stays legible when 80 functions all call the same thing.
 */
function runLayout(cy: Core, mode: Exclude<LayoutMode, "auto">, centerId: string | null, animate: boolean) {
  if (cy.nodes().length === 0) return;
  // Cytoscape measures its container once and caches it. If that happened before the page
  // finished laying out, the size is stale (it read 0 x 245 here) and "fit" zooms to nonsense.
  cy.resize();
  if (mode === "rings") {
    const center = centerId ? cy.getElementById(centerId) : cy.collection();
    cy.nodes().data("hop", 9);
    if (center.nonempty()) {
      center.data("hop", 0);
      cy.elements().bfs({
        roots: center,
        directed: false,
        visit: (v, _e, _u, _i, depth) => {
          v.data("hop", depth);
        },
      });
    }
    cy.layout({
      name: "concentric",
      concentric: (node: cytoscape.NodeSingular) => 20 - Number(node.data("hop")),
      levelWidth: () => 1,
      minNodeSpacing: 18,
      spacingFactor: 1.1,
      avoidOverlap: true,
      startAngle: (3 / 2) * Math.PI,
      animate,
      animationDuration: 250,
      fit: true,
      padding: 44,
    } as cytoscape.LayoutOptions).run();
    return;
  }
  cy.layout({
    name: "dagre",
    rankDir: "LR",
    nodeSep: 26,
    rankSep: 90,
    edgeSep: 12,
    nodeDimensionsIncludeLabels: true,
    animate,
    animationDuration: 250,
    fit: true,
    padding: 40,
  } as cytoscape.LayoutOptions).run();
}

// Registered once, at module scope. Registering inside a component re-registers
// on every mount and throws under React strict mode.
cytoscape.use(dagre);

interface Props {
  data: GraphResponse | undefined;
  selectedId: string | null;
  /** fqn → hops from the changed function, for the shockwave overlay. */
  distances: Map<string, number> | null;
  showImpact: boolean;
  onSelect: (fqn: string) => void;
  themeKey: string;
  loading?: boolean;
}

interface Tip {
  x: number;
  y: number;
  id: string;
  name: string;
  module: string;
  complexity: number | null;
  line: number | null;
  isTest: boolean;
  isEntrypoint: boolean;
}

function tipFor(node: cytoscape.NodeSingular): Tip {
  const p = node.renderedPosition();
  return {
    x: p.x,
    y: p.y,
    id: node.id(),
    name: node.data("label") as string,
    module: node.data("module") as string,
    complexity: node.data("complexity") as number,
    line: (node.data("line") as number | null) ?? null,
    isTest: !!node.data("is_test"),
    isEntrypoint: !!node.data("is_entrypoint"),
  };
}

const toElements = (data: GraphResponse): ElementDefinition[] => [
  ...data.nodes.map((n) => ({
    group: "nodes" as const,
    data: {
      id: n.id,
      label: n.name,
      module: n.module ?? "",
      complexity: n.complexity ?? 1,
      is_test: n.is_test,
      is_entrypoint: n.is_entrypoint,
      line: n.line,
      wave: 0,
    },
  })),
  ...data.edges.map((e, i) => ({
    group: "edges" as const,
    data: {
      id: `e${i}:${e.source}>${e.target}:${e.line ?? 0}`,
      source: e.source,
      target: e.target,
      confidence: e.confidence,
      resolution: e.resolution ?? "exact",
      line: e.line,
    },
  })),
];

export function GraphCanvas({ data, selectedId, distances, showImpact, onSelect, themeKey, loading }: Props) {
  const container = useRef<HTMLDivElement | null>(null);
  const cyRef = useRef<Core | null>(null);
  const onSelectRef = useRef(onSelect);
  /** True once the user has panned or zoomed: from then on we stop re-fitting on resize. */
  const viewMoved = useRef(false);
  const [tip, setTip] = useState<Tip | null>(null);
  const [layoutMode, setLayoutMode] = useState<LayoutMode>("auto");
  const [ripple, setRipple] = useState<{ x: number; y: number; r: number } | null>(null);

  useEffect(() => {
    onSelectRef.current = onSelect;
  }, [onSelect]);

  // Create the instance once; destroy it on unmount or it leaks on every navigation.
  useEffect(() => {
    if (!container.current) return;
    const cy = cytoscape({
      container: container.current,
      elements: [],
      style: buildStylesheet(readTheme()),
      minZoom: 0.15,
      maxZoom: 2.5,
      wheelSensitivity: 0.25,
      // Deliberately NOT hideEdgesOnViewport / textureOnViewport: they make every edge
      // vanish while you drag a node or pan, which reads as broken. The server caps the
      // graph at 90 nodes, so drawing every edge on every frame is cheap.
      pixelRatio: "auto",
    });
    cyRef.current = cy;

    // The container's size can change after the first layout (the page settles, the window
    // is resized). Re-measure, and keep the whole graph in view unless the user has taken over.
    const observer = new ResizeObserver(() => {
      cy.resize();
      if (!viewMoved.current && cy.nodes().length > 0) cy.fit(undefined, FIT_PADDING);
    });
    observer.observe(container.current);
    const markMoved = () => {
      viewMoved.current = true;
    };
    const host = container.current;
    host.addEventListener("wheel", markMoved, { passive: true });
    host.addEventListener("pointerdown", markMoved);

    cy.on("tap", "node", (event: EventObject) => onSelectRef.current(event.target.id() as string));

    const setCursor = (value: string) => {
      if (container.current) container.current.style.cursor = value;
    };

    cy.on("mouseover", "node", (event: EventObject) => {
      const node = event.target;
      node.addClass("hover");
      node.connectedEdges().addClass("hover");
      setCursor("grab");
      setTip(tipFor(node));
    });
    cy.on("mouseout", "node", (event: EventObject) => {
      event.target.removeClass("hover");
      event.target.connectedEdges().removeClass("hover");
      setCursor("");
      setTip(null);
    });
    cy.on("viewport", () => setTip(null));

    // Dragging a node: its edges stay attached and follow it. The tooltip would be left
    // behind at the old position, so hide it while the node is held and restore it on release.
    cy.on("grab", "node", () => {
      setCursor("grabbing");
      setTip(null);
    });
    cy.on("free", "node", (event: EventObject) => {
      setCursor("grab");
      setTip(tipFor(event.target));
    });

    // Labels are only legible when zoomed in; when zoomed out they are noise.
    const syncLabels = () => cy.nodes().toggleClass("nolabel", cy.zoom() < LABEL_MIN_ZOOM);
    cy.on("zoom layoutstop add", syncLabels);

    return () => {
      observer.disconnect();
      host.removeEventListener("wheel", markMoved);
      host.removeEventListener("pointerdown", markMoved);
      cy.destroy();
      cyRef.current = null;
    };
  }, []);

  // Restyle when the light/dark theme flips.
  useEffect(() => {
    cyRef.current?.style(buildStylesheet(readTheme()));
  }, [themeKey]);

  const effectiveMode: Exclude<LayoutMode, "auto"> =
    layoutMode === "auto" ? ((data?.nodes.length ?? 0) > RINGS_THRESHOLD ? "rings" : "flow") : layoutMode;

  // Replace the elements. Batched: a 300-node update stutters without it.
  useEffect(() => {
    const cy = cyRef.current;
    if (!cy || !data) return;
    cy.batch(() => {
      cy.elements().remove();
      cy.add(toElements(data));
    });
  }, [data]);

  // Lay out whenever the elements or the chosen layout change.
  useEffect(() => {
    const cy = cyRef.current;
    if (!cy || !data) return;
    viewMoved.current = false; // a fresh layout is fitted to the view again
    runLayout(cy, effectiveMode, selectedId, false);
  }, [data, effectiveMode, selectedId]);

  // Selection and the shockwave overlay are style-only changes: no relayout.
  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;
    cy.batch(() => {
      cy.nodes().removeClass("selected");
      if (selectedId) cy.getElementById(selectedId).addClass("selected");
      cy.nodes().forEach((node) => {
        const hops = showImpact ? (distances?.get(node.id()) ?? 0) : 0;
        node.data("wave", hops);
      });
    });
  }, [selectedId, distances, showImpact, data]);

  // A ripple that emanates from the selected node, positioned in screen space.
  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;
    const place = () => {
      const node = selectedId ? cy.getElementById(selectedId) : null;
      if (!node || node.empty()) return setRipple(null);
      const p = node.renderedPosition();
      setRipple({ x: p.x, y: p.y, r: Math.max(18, node.renderedWidth() * 0.9) });
    };
    place();
    cy.on("render layoutstop", place);
    return () => {
      cy.off("render layoutstop", place);
    };
  }, [selectedId, data]);

  const shown = data?.nodes.length ?? 0;
  const label = useMemo(
    () => (data?.truncated ? `Showing ${shown} of ${data.total} — raise the confidence filter to narrow` : null),
    [data, shown],
  );

  return (
    <div className="relative h-full w-full overflow-hidden rounded-xl border border-line bg-surface">
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 opacity-[0.35]"
        style={{
          backgroundImage: "radial-gradient(rgb(var(--line)) 1px, transparent 1px)",
          backgroundSize: "22px 22px",
        }}
      />
      <div ref={container} className="absolute inset-0" role="img" aria-label="Call graph" />

      {showImpact && ripple && (
        <div className="pointer-events-none absolute inset-0 overflow-hidden" aria-hidden>
          {[0, 0.8, 1.6].map((delay) => (
            <span
              key={delay}
              className="absolute animate-ripple rounded-full border-2 border-accent"
              style={{
                left: ripple.x - ripple.r,
                top: ripple.y - ripple.r,
                width: ripple.r * 2,
                height: ripple.r * 2,
                animationDelay: `${delay}s`,
              }}
            />
          ))}
        </div>
      )}

      {tip && (
        <div
          className="pointer-events-none absolute z-30 max-w-xs -translate-x-1/2 rounded-lg border border-line bg-raised px-3 py-2 shadow-card"
          style={{ left: tip.x, top: tip.y + 28 }}
        >
          <div className="font-mono text-xs font-semibold text-ink">{tip.name}</div>
          <div className="mt-0.5 break-all font-mono text-2xs text-soft">{shortFqn(tip.id)}</div>
          <div className="mt-1.5 flex flex-wrap gap-x-3 gap-y-0.5 text-2xs text-faint">
            <span>{tip.module}{tip.line ? `:${tip.line}` : ""}</span>
            <span>complexity {tip.complexity}</span>
            {tip.isTest && <span className="text-ok">test</span>}
            {tip.isEntrypoint && <span className="text-info">entrypoint</span>}
          </div>
        </div>
      )}

      <div className="absolute right-3 top-3 z-20 flex gap-1.5">
        <div className="flex overflow-hidden rounded-lg border border-line bg-raised" role="group" aria-label="Layout">
          {(["flow", "rings"] as const).map((mode) => (
            <button
              key={mode}
              aria-pressed={effectiveMode === mode}
              onClick={() => setLayoutMode(mode)}
              title={mode === "flow" ? "Callers on the left, callees on the right" : "Rings by distance from the selected function"}
              className={
                "px-2.5 py-1.5 text-xs font-medium capitalize transition-colors " +
                (effectiveMode === mode ? "bg-accent/15 text-accent" : "text-soft hover:text-ink")
              }
            >
              {mode}
            </button>
          ))}
        </div>
        <button
          className="btn px-2"
          title="Fit to view"
          aria-label="Fit to view"
          onClick={() => {
            viewMoved.current = false;
            cyRef.current?.resize();
            cyRef.current?.fit(undefined, FIT_PADDING);
          }}
        >
          <Maximize2 size={15} />
        </button>
        <button
          className="btn px-2"
          title="Reset layout"
          aria-label="Reset layout"
          onClick={() => {
            const cy = cyRef.current;
            if (!cy) return;
            viewMoved.current = false;
            runLayout(cy, effectiveMode, selectedId, true);
          }}
        >
          <RotateCcw size={15} />
        </button>
      </div>

      <Legend />

      {label && (
        <div className="absolute left-3 top-3 z-20 rounded-lg border border-warn/40 bg-warn/10 px-3 py-1.5 text-xs text-warn">
          {label}
        </div>
      )}
      {loading && (
        <div className="absolute inset-x-0 top-0 z-20 h-0.5 overflow-hidden bg-line">
          <div className="h-full w-1/3 animate-pulse bg-accent" />
        </div>
      )}
    </div>
  );
}

function Legend() {
  return (
    <div className="absolute bottom-3 left-3 z-20 flex flex-wrap items-center gap-x-4 gap-y-1 rounded-lg border border-line bg-raised/90 px-3 py-2 text-2xs text-soft backdrop-blur">
      <span className="flex items-center gap-1.5">
        <svg width="26" height="8" aria-hidden><line x1="0" y1="4" x2="26" y2="4" stroke="currentColor" strokeWidth="2" /></svg>
        exact
      </span>
      <span className="flex items-center gap-1.5">
        <svg width="26" height="8" aria-hidden><line x1="0" y1="4" x2="26" y2="4" stroke="currentColor" strokeWidth="2" strokeDasharray="6 4" /></svg>
        inferred
      </span>
      <span className="flex items-center gap-1.5">
        <svg width="26" height="8" aria-hidden><line x1="0" y1="4" x2="26" y2="4" stroke="currentColor" strokeWidth="2.4" strokeDasharray="1 4" strokeLinecap="round" /></svg>
        ambiguous
      </span>
      <span className="flex items-center gap-1.5">
        <span className="inline-block h-2.5 w-2.5 rotate-45 border-2 border-ok" /> test
      </span>
      <span className="flex items-center gap-1.5">
        <span className="inline-block h-2.5 w-2.5 rounded-full border-[3px] border-info" /> entrypoint
      </span>
      <span className="text-faint">edge fade = confidence ({formatConfidence(0.3)}–1)</span>
      <span className="basis-full text-faint">
        Arrows point from a function to the one it calls. Drag a circle to move it, click one to make it the
        centre, scroll to zoom.
      </span>
    </div>
  );
}
