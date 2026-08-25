/**
 * MermaidDiagram.tsx — renders a create_diagram tool result inline,
 * interactively.
 *
 * Mermaid is lazy-imported (heavy dependency — only users who actually
 * receive a diagram pay the bundle cost). Render errors degrade gracefully
 * to showing the source in a code block, so a model syntax slip never
 * breaks the message bubble.
 *
 * Interaction layer (custom Pointer Events — no extra dependency; the
 * usual svg-pan-zoom lib needs hammer.js for touch anyway):
 *   • drag to pan (mouse or single finger)
 *   • wheel / trackpad to zoom toward the cursor
 *   • two-finger pinch to zoom (touch)
 *   • double-click / double-tap to zoom in
 *   • toolbar: zoom −/＋, reset view, fullscreen expand, download .svg
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Check, Copy, Download, Expand, Minimize2, Minus, MoreHorizontal, Plus, RotateCcw } from "lucide-react";
import type { MessageDiagram } from "@/types/memory";
import { prepareMermaidSource } from "@/lib/mermaidSource";

let mermaidReady: Promise<typeof import("mermaid")["default"]> | null = null;

function loadMermaid() {
  if (!mermaidReady) {
    mermaidReady = import("mermaid").then((m) => {
      m.default.initialize({
        startOnLoad: false,
        // Never let Mermaid inject its own red parser-error SVG into the chat.
        // Rendering failures must stay inside this component's controlled
        // fallback UI so one malformed model diagram cannot pollute the turn.
        suppressErrorRendering: true,
        theme: "dark",
        themeVariables: {
          // Match the app's dark palette (bg ~ #0b0e14, gold accent).
          background: "transparent",
          primaryColor: "#1f2430",
          primaryTextColor: "#e6e1d7",
          primaryBorderColor: "#c9a86a",
          lineColor: "#8a8577",
          secondaryColor: "#141821",
          tertiaryColor: "#141821",
        },
        securityLevel: "strict",
        mindmap: { useMaxWidth: true },
        flowchart: { useMaxWidth: true },
      });
      return m.default;
    });
  }
  return mermaidReady;
}

let renderSeq = 0;

const MIN_SCALE = 0.4;
const MAX_SCALE = 6;

interface ViewState {
  x: number;
  y: number;
  scale: number;
}

export default function MermaidDiagram({
  diagram,
  onNodeAsk,
}: {
  diagram: MessageDiagram;
  onNodeAsk?: (prompt: string) => void;
}) {
  const [svg, setSvg] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState(false);
  const prepared = useMemo(() => prepareMermaidSource(diagram.mermaid), [diagram.mermaid]);

  useEffect(() => {
    let cancelled = false;
    setError(null);
    setSvg(null);
    loadMermaid()
      .then(async (mermaid) => {
        const id = `smara-diagram-${++renderSeq}`;
        const { svg: rendered } = await mermaid.render(id, prepared.source);
        if (!rendered || /Syntax error in text|mermaid version/i.test(rendered)) {
          throw new Error("Mermaid returned an error graphic instead of a diagram.");
        }
        if (!cancelled) setSvg(rendered);
      })
      .catch((e) => {
        if (!cancelled) setError(e?.message || "Diagram failed to render.");
      });
    return () => {
      cancelled = true;
    };
  }, [prepared.source]);

  // Close fullscreen on Escape.
  useEffect(() => {
    if (!expanded) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setExpanded(false);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [expanded]);

  const downloadSvg = useCallback(() => {
    if (!svg) return;
    const blob = new Blob([svg], { type: "image/svg+xml" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${(diagram.title || "diagram").replace(/[^\w-]+/g, "_").toLowerCase()}.svg`;
    a.click();
    URL.revokeObjectURL(url);
  }, [svg, diagram.title]);

  if (error) {
    return (
      <div
        className="mt-3 rounded-xl p-3"
        style={{ border: "1px solid var(--border-accent)", background: "var(--bg-elevated)" }}
      >
        <div className="mb-2 text-xs font-medium uppercase tracking-wide" style={{ color: "var(--text-muted)" }}>
          {diagram.title}
        </div>
        <div className="mb-2 text-xs" style={{ color: "var(--error, #f87171)" }}>
          Couldn't render this visual. The source is preserved below so you can retry or copy it.
        </div>
        {prepared.changed && (
          <div className="mb-2 text-[11px]" style={{ color: "var(--accent)" }}>
            Smara automatically made the flowchart labels safe for Mermaid.
          </div>
        )}
        <pre
          className="overflow-x-auto rounded p-2 text-xs"
          style={{ background: "rgba(0,0,0,0.3)", color: "var(--text-muted)" }}
        >
          {prepared.source}
        </pre>
      </div>
    );
  }

  const card = (
    <PanZoomCard
      title={diagram.title}
      kind={diagram.kind}
      svg={svg}
      source={prepared.source}
      autoFixed={prepared.changed}
      onNodeAsk={onNodeAsk}
      expanded={expanded}
      onToggleExpand={() => setExpanded((v) => !v)}
      onDownload={downloadSvg}
    />
  );

  return (
    <>
      {!expanded && card}
      {expanded && (
        <>
          {/* keep a placeholder so the message layout doesn't jump */}
          <div
            className="mt-3 rounded-xl p-3 text-xs"
            style={{ border: "1px dashed var(--border-dim)", color: "var(--text-muted)" }}
          >
            {diagram.title} — open in fullscreen
          </div>
          <div
            className="fixed inset-0 z-50 flex flex-col p-4 sm:p-8"
            style={{ background: "rgba(5,7,12,0.88)", backdropFilter: "blur(4px)" }}
            onClick={(e) => {
              if (e.target === e.currentTarget) setExpanded(false);
            }}
          >
            {card}
          </div>
        </>
      )}
    </>
  );
}

function PanZoomCard({
  title,
  kind,
  svg,
  source,
  autoFixed,
  onNodeAsk,
  expanded,
  onToggleExpand,
  onDownload,
}: {
  title: string;
  kind: string;
  svg: string | null;
  source: string;
  autoFixed: boolean;
  onNodeAsk?: (prompt: string) => void;
  expanded: boolean;
  onToggleExpand: () => void;
  onDownload: () => void;
}) {
  const viewportRef = useRef<HTMLDivElement>(null);
  const contentRef = useRef<HTMLDivElement>(null);
  const view = useRef<ViewState>({ x: 0, y: 0, scale: 1 });
  // Active pointers for pan/pinch (Pointer Events unify mouse + touch).
  const pointers = useRef<Map<number, { x: number; y: number }>>(new Map());
  const pinchStart = useRef<{ dist: number; scale: number } | null>(null);
  const [interacted, setInteracted] = useState(false);
  const [zoomPercent, setZoomPercent] = useState(100);
  const [menuOpen, setMenuOpen] = useState(false);
  const [copied, setCopied] = useState(false);

  const apply = useCallback(() => {
    const el = contentRef.current;
    if (el) {
      const { x, y, scale } = view.current;
      el.style.transform = `translate(${x}px, ${y}px) scale(${scale})`;
      setZoomPercent(Math.round(scale * 100));
    }
  }, []);

  const reset = useCallback(() => {
    view.current = { x: 0, y: 0, scale: 1 };
    setInteracted(false);
    apply();
  }, [apply]);

  // Reset the view when the svg changes or fullscreen toggles.
  useEffect(() => {
    reset();
  }, [svg, expanded, reset]);

  // Mermaid renders nodes as SVG groups. Turn them into conversational
  // affordances: clicking a node puts a focused follow-up in the composer.
  useEffect(() => {
    const root = contentRef.current;
    if (!root || !svg || !onNodeAsk) return;
    const nodes = Array.from(root.querySelectorAll<SVGGElement>(".node"));
    const cleanups = nodes.map((node) => {
      node.classList.add("visual-node");
      const handleClick = (event: Event) => {
        event.stopPropagation();
        const label = (node.textContent || "").replace(/\s+/g, " ").trim();
        if (label) onNodeAsk(`Tell me more about “${label}” in this visual.`);
      };
      node.addEventListener("click", handleClick);
      return () => node.removeEventListener("click", handleClick);
    });
    return () => cleanups.forEach((cleanup) => cleanup());
  }, [svg, onNodeAsk]);

  const copySource = async () => {
    try {
      await navigator.clipboard.writeText(source);
      setCopied(true);
      setTimeout(() => setCopied(false), 1400);
    } catch {
      // Clipboard is an enhancement; the visual remains fully usable.
    }
  };

  const zoomAt = useCallback(
    (clientX: number, clientY: number, factor: number) => {
      const vp = viewportRef.current;
      if (!vp) return;
      const rect = vp.getBoundingClientRect();
      const px = clientX - rect.left;
      const py = clientY - rect.top;
      const v = view.current;
      const next = Math.min(MAX_SCALE, Math.max(MIN_SCALE, v.scale * factor));
      const real = next / v.scale;
      // Keep the point under the cursor stationary while scaling.
      v.x = px - (px - v.x) * real;
      v.y = py - (py - v.y) * real;
      v.scale = next;
      setInteracted(true);
      apply();
    },
    [apply],
  );

  const zoomCenter = useCallback(
    (factor: number) => {
      const vp = viewportRef.current;
      if (!vp) return;
      const r = vp.getBoundingClientRect();
      zoomAt(r.left + r.width / 2, r.top + r.height / 2, factor);
    },
    [zoomAt],
  );

  // Wheel zoom needs a NON-passive listener (React's onWheel is passive, so
  // preventDefault there is ignored and the page scrolls instead of zooming).
  useEffect(() => {
    const vp = viewportRef.current;
    if (!vp) return;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      zoomAt(e.clientX, e.clientY, e.deltaY < 0 ? 1.15 : 1 / 1.15);
    };
    vp.addEventListener("wheel", onWheel, { passive: false });
    return () => vp.removeEventListener("wheel", onWheel);
  }, [zoomAt, svg]);

  const onPointerDown = (e: React.PointerEvent) => {
    (e.target as Element).setPointerCapture?.(e.pointerId);
    pointers.current.set(e.pointerId, { x: e.clientX, y: e.clientY });
    if (pointers.current.size === 2) {
      const [a, b] = [...pointers.current.values()];
      pinchStart.current = { dist: Math.hypot(a.x - b.x, a.y - b.y), scale: view.current.scale };
    }
  };

  const onPointerMove = (e: React.PointerEvent) => {
    const prev = pointers.current.get(e.pointerId);
    if (!prev) return;
    const cur = { x: e.clientX, y: e.clientY };
    pointers.current.set(e.pointerId, cur);

    if (pointers.current.size === 2 && pinchStart.current) {
      // Pinch: zoom toward the midpoint of the two fingers.
      const [a, b] = [...pointers.current.values()];
      const dist = Math.hypot(a.x - b.x, a.y - b.y);
      const target = Math.min(
        MAX_SCALE,
        Math.max(MIN_SCALE, pinchStart.current.scale * (dist / pinchStart.current.dist)),
      );
      const midX = (a.x + b.x) / 2;
      const midY = (a.y + b.y) / 2;
      zoomAt(midX, midY, target / view.current.scale);
    } else if (pointers.current.size === 1) {
      view.current.x += cur.x - prev.x;
      view.current.y += cur.y - prev.y;
      setInteracted(true);
      apply();
    }
  };

  const onPointerUp = (e: React.PointerEvent) => {
    pointers.current.delete(e.pointerId);
    if (pointers.current.size < 2) pinchStart.current = null;
  };

  const btnStyle: React.CSSProperties = {
    color: "var(--text-muted)",
  };

  return (
    <div
      className={
        expanded
          ? "flex min-h-0 flex-1 flex-col rounded-xl p-3"
          : "mt-3 flex flex-col rounded-xl p-3"
      }
      // Keep the diagram canvas visually continuous with the conversation.
      // The dotted visual-stage remains the only framed surface; toolbar
      // controls stay visible without the extra curved card boundary.
      style={{ background: "transparent" }}
      onClick={(e) => expanded && e.stopPropagation()}
    >
      <div className="mb-2 flex items-center gap-1.5">
        <div
          className="min-w-0 flex-1 truncate text-xs font-medium tracking-wide"
          style={{ color: "var(--text-muted)" }}
        >
          <span>{title}</span>
          <span className="visual-kind ml-2">{kind.replace(/diagram$/, "")}</span>
          {autoFixed && <span className="visual-fixed ml-2">auto-fixed</span>}
        </div>
        <span className="hidden sm:inline text-[10px] tabular-nums" style={{ color: "var(--text-dim)" }}>{zoomPercent}%</span>
        <ToolbarButton title="Zoom out" onClick={() => zoomCenter(1 / 1.3)} style={btnStyle}>
          <Minus size={13} />
        </ToolbarButton>
        <ToolbarButton title="Zoom in" onClick={() => zoomCenter(1.3)} style={btnStyle}>
          <Plus size={13} />
        </ToolbarButton>
        {interacted && (
          <ToolbarButton title="Reset view" onClick={reset} style={btnStyle}>
            <RotateCcw size={13} />
          </ToolbarButton>
        )}
        <ToolbarButton title="Download SVG" onClick={onDownload} style={btnStyle}>
          <Download size={13} />
        </ToolbarButton>
        <div className="relative">
          <ToolbarButton title="More visual actions" onClick={() => setMenuOpen((v) => !v)} style={btnStyle}>
            <MoreHorizontal size={13} />
          </ToolbarButton>
          {menuOpen && (
            <div className="visual-menu absolute right-0 top-8 z-10 min-w-[150px] rounded-xl p-1.5" onClick={(e) => e.stopPropagation()}>
              <button className="visual-menu-item" onClick={copySource}>
                {copied ? <Check size={13} /> : <Copy size={13} />}
                {copied ? "Copied" : "Copy source"}
              </button>
              <button className="visual-menu-item" onClick={onDownload}>
                <Download size={13} /> Download SVG
              </button>
            </div>
          )}
        </div>
        <ToolbarButton
          title={expanded ? "Exit fullscreen (Esc)" : "Fullscreen"}
          onClick={onToggleExpand}
          style={btnStyle}
        >
          {expanded ? <Minimize2 size={13} /> : <Expand size={13} />}
        </ToolbarButton>
      </div>

      {svg === null ? (
        <div className="py-6 text-center text-xs" style={{ color: "var(--text-muted)" }}>
          Rendering diagram…
        </div>
      ) : (
        <div
          ref={viewportRef}
          className={expanded ? "visual-stage min-h-0 flex-1 overflow-hidden rounded" : "visual-stage overflow-hidden rounded"}
          style={{
            touchAction: "none",           // we own all touch gestures here
            cursor: "grab",
            maxHeight: expanded ? undefined : 480,
          }}
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={onPointerUp}
          onPointerCancel={onPointerUp}
          onDoubleClick={(e) => zoomAt(e.clientX, e.clientY, 1.5)}
        >
          <div
            ref={contentRef}
            style={{ transformOrigin: "0 0", willChange: "transform" }}
            className="[&_svg]:mx-auto [&_svg]:max-w-full"
            dangerouslySetInnerHTML={{ __html: svg }}
          />
        </div>
      )}
      <div className="mt-1.5 text-center text-[10px]" style={{ color: "var(--text-dim, var(--text-muted))" }}>
        click a node to ask a follow-up · drag to pan · scroll or pinch to zoom
      </div>
    </div>
  );
}

function ToolbarButton({
  title,
  onClick,
  style,
  children,
}: {
  title: string;
  onClick: () => void;
  style: React.CSSProperties;
  children: React.ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      title={title}
      aria-label={title}
      className="grid h-6 w-6 shrink-0 place-items-center rounded-md transition-colors duration-150 hover:bg-[var(--bg-card)]"
      style={style}
    >
      {children}
    </button>
  );
}
