import {
  Activity,
  AlertTriangle,
  ArrowLeft,
  Inbox,
  LayoutDashboard,
  ListChecks,
  Loader2,
  RefreshCcw,
  Search,
  Workflow,
} from "lucide-react";
import { AnimatePresence, motion } from "motion/react";
import { useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";

import { DashboardCard, DashboardShell } from "../components/DashboardShell";
import "../styles.css";

type Lang = "zh" | "en";
type AnyRecord = Record<string, unknown>;
type CanvasView = "overview" | "module" | "technical";
type Tone = "done" | "current" | "blocked" | "pending" | "info";

type CanvasModule = {
  module?: string;
  status?: string;
  updated?: string;
  owner?: string;
};

type CanvasCandidate = {
  name?: string;
  target_module?: string;
  summary?: string;
  reason?: string;
  review_state?: string;
  evidence_count?: number;
  source?: string;
  candidate_source?: string;
  confidence?: string;
};

type CanvasData = {
  ok?: boolean;
  source?: string;
  source_path?: string;
  source_hash?: string;
  source_updated_at?: string | null;
  updated?: string;
  mermaid_src?: string;
  active_modules?: CanvasModule[];
  canvas_candidates?: CanvasCandidate[];
  candidate_count?: number;
  candidate_warnings?: string[];
  warnings?: string[];
  errors?: string[];
  error?: string | null;
  generated_at?: string;
  cached?: boolean;
  repo_dirty?: boolean;
};

const copy = {
  zh: {
    tagline: "你的 AI 第二大脑",
    badge: "项目进展",
    title: "项目进展",
    intro: "渲染 project-canvas.md 的项目画布、活跃模块和待纳入候选。当前为只读视图。",
    steward: "记忆管家",
    loaded: "已加载",
    unavailable: "不可用",
    updated: "更新",
    source: "来源",
    refresh: "刷新",
    refreshing: "正在刷新",
    statusMap: "项目状态图",
    overview: "画布",
    modules: "模块视图",
    technical: "技术图",
    candidates: "待纳入画布",
    candidatesIntro: "来自交接卡的只读候选，需人工确认后才会进入已验证模块。",
    activeModules: "活跃模块",
    activeIntro: "页面读取源文件中的活跃模块表，仅展示最近状态。",
    moduleCount: "共 {count} 项",
    candidateCount: "{count} 项",
    emptyModules: "暂无活跃模块摘要，请先更新画布。",
    emptyCandidates: "暂无待纳入候选。",
    noSelection: "请先从右侧活跃模块点击任一模块查看。",
    backOverview: "返回总览",
    owner: "负责人",
    moduleStatus: "状态",
    moduleUpdated: "最近更新",
    targetModule: "目标模块",
    reason: "原因",
    evidence: "证据",
    confidence: "置信度",
    graphHint: "拖拽空白处移动，滚轮缩放；点击节点查看详情。",
    zoomIn: "放大",
    zoomOut: "缩小",
    reset: "复位",
    technicalHint: "技术视图保留 project-canvas.md 中的 Mermaid 源码，方便和旧版数据源对照。",
    warnings: "提示",
    errors: "错误",
  },
  en: {
    tagline: "Your AI second brain",
    badge: "Projects",
    title: "Project Canvas",
    intro: "Render project-canvas.md as a read-only project canvas, active module list, and candidate shelf.",
    steward: "Memory steward",
    loaded: "Loaded",
    unavailable: "Unavailable",
    updated: "Updated",
    source: "Source",
    refresh: "Refresh",
    refreshing: "Refreshing",
    statusMap: "Project status map",
    overview: "Map",
    modules: "Modules",
    technical: "Technical",
    candidates: "Canvas candidates",
    candidatesIntro: "Read-only handoff candidates. They enter the verified map only after human review.",
    activeModules: "Active modules",
    activeIntro: "Reads the active module table from the source canvas.",
    moduleCount: "{count} items",
    candidateCount: "{count} items",
    emptyModules: "No active module summary.",
    emptyCandidates: "No candidate updates.",
    noSelection: "Select an active module from the right side first.",
    backOverview: "Back to map",
    owner: "Owner",
    moduleStatus: "Status",
    moduleUpdated: "Updated",
    targetModule: "Target module",
    reason: "Reason",
    evidence: "Evidence",
    confidence: "Confidence",
    graphHint: "Drag empty space to pan, wheel to zoom, and click nodes for details.",
    zoomIn: "Zoom in",
    zoomOut: "Zoom out",
    reset: "Reset",
    technicalHint: "Technical view keeps the Mermaid source from project-canvas.md for comparison with the legacy data source.",
    warnings: "Warnings",
    errors: "Errors",
  },
} as const;

function readJsonScript(id: string): AnyRecord {
  const node = document.getElementById(id);
  const text = node?.textContent?.trim();
  if (!text || text.startsWith("__TM_")) return {};
  try {
    return JSON.parse(text) as AnyRecord;
  } catch {
    return {};
  }
}

function initialLanguage(): Lang {
  const stored = window.localStorage.getItem("tm-lang");
  if (stored === "zh" || stored === "en") return stored;
  return window.navigator.language.toLowerCase().startsWith("en") ? "en" : "zh";
}

function text(value: unknown, fallback = "-") {
  if (value === null || value === undefined || value === "") return fallback;
  return String(value);
}

function friendlyCanvasWarning(value: unknown) {
  const message = text(value, "");
  if (message.includes("mem0_request blocked") || message.includes("local profile")) {
    return "当前是 local profile，本地模式不会请求高级 Mem0 HTTP；待纳入候选已降级读取 inbox/wiki_proposal。";
  }
  return message;
}

function format(template: string, values: Record<string, string | number>) {
  return Object.entries(values).reduce((current, [key, value]) => current.replace(`{${key}}`, String(value)), template);
}

function statusTone(value: unknown): Tone {
  const normalized = text(value, "").toLowerCase();
  if (normalized.includes("完成") || normalized.includes("✅") || normalized.includes("done") || normalized.includes("closed")) return "done";
  if (normalized.includes("阻塞") || normalized.includes("block") || normalized.includes("🔴")) return "blocked";
  if (normalized.includes("待") || normalized.includes("⚪") || normalized.includes("pending")) return "pending";
  if (normalized.includes("进行") || normalized.includes("推进") || normalized.includes("🟡") || normalized.includes("active")) return "current";
  return "info";
}

function toneClass(tone: Tone) {
  if (tone === "done") return "border-tm-ok-border bg-tm-ok-bg text-tm-ok";
  if (tone === "blocked") return "border-tm-fail-border bg-tm-fail-bg text-tm-fail";
  if (tone === "current") return "border-tm-warn-border bg-tm-warn-bg text-tm-warn";
  if (tone === "pending") return "border-tm-border bg-tm-card-alt text-tm-tertiary";
  return "border-tm-border bg-tm-info-bg text-tm-secondary";
}

function StatusPill({ status, label }: { status: unknown; label?: string }) {
  return <span className={`rounded-full border px-2.5 py-1 text-xs font-semibold ${toneClass(statusTone(status))}`}>{label || text(status)}</span>;
}

function App() {
  const initialData = useMemo(() => readJsonScript("tm-canvas-data") as CanvasData, []);
  const [lang, setLang] = useState<Lang>(initialLanguage);
  const [data, setData] = useState<CanvasData>(initialData);
  const [view, setView] = useState<CanvasView>("overview");
  const [selectedIndex, setSelectedIndex] = useState(0);
  const [refreshing, setRefreshing] = useState(false);
  const t = (key: keyof typeof copy.zh) => copy[lang][key];
  const modules = Array.isArray(data.active_modules) ? data.active_modules : [];
  const candidates = Array.isArray(data.canvas_candidates) ? data.canvas_candidates : [];
  const selectedModule = modules[selectedIndex] || null;

  function toggleLang() {
    setLang((current) => {
      const next = current === "zh" ? "en" : "zh";
      window.localStorage.setItem("tm-lang", next);
      return next;
    });
  }

  async function refresh() {
    setRefreshing(true);
    try {
      const res = await fetch("/api/canvas");
      const next = (await res.json()) as CanvasData;
      setData(next);
      setSelectedIndex(0);
    } finally {
      setRefreshing(false);
    }
  }

  useEffect(() => {
    if (selectedIndex >= modules.length) setSelectedIndex(0);
  }, [modules.length, selectedIndex]);

  return (
    <DashboardShell active="/canvas" lang={lang} onToggleLang={toggleLang} tagline={t("tagline")} badge={t("badge")} background="galaxy">
      <main className="relative z-10 mx-auto max-w-6xl px-6 py-8">
        <motion.section initial={{ opacity: 0, y: 14 }} animate={{ opacity: 1, y: 0 }} className="mb-6 rounded-2xl border border-tm-border bg-tm-card p-5 shadow-[0_1px_2px_rgba(31,29,27,0.04),0_12px_32px_rgba(168,123,34,0.06)]">
          <div className="flex flex-col gap-4 md:flex-row md:items-end md:justify-between">
            <div>
              <div className="mb-2 inline-flex items-center gap-2 text-sm font-medium text-tm-tertiary">
                <Activity size={16} className="text-tm-accent" />
                {t("steward")}
              </div>
              <h1 className="text-4xl font-extrabold tracking-normal text-tm-primary">{t("title")}</h1>
              <p className="mt-2 max-w-3xl text-sm leading-6 text-tm-secondary">{t("intro")}</p>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <StatusPill status={data.ok ? "done" : "current"} label={data.ok ? t("loaded") : t("unavailable")} />
              <span className="rounded-full border border-tm-border bg-tm-card-alt px-3 py-1 text-xs text-tm-secondary">{t("updated")}：{text(data.updated)}</span>
              <button
                type="button"
                onClick={() => void refresh()}
                disabled={refreshing}
                className="inline-flex h-9 items-center gap-2 rounded-xl border border-tm-border bg-tm-card-alt px-3 text-sm font-semibold text-tm-secondary hover:border-tm-accent disabled:opacity-60"
              >
                {refreshing ? <Loader2 size={15} className="animate-spin" /> : <RefreshCcw size={15} />}
                {refreshing ? t("refreshing") : t("refresh")}
              </button>
            </div>
          </div>
          <div className="mt-4 grid gap-2 text-xs text-tm-secondary md:grid-cols-2">
            <InfoBadge label={t("source")} value={data.source_path || data.source} />
            <InfoBadge label="hash" value={data.source_hash ? text(data.source_hash).slice(0, 12) : "-"} />
          </div>
          <MessageList title={t("warnings")} items={data.warnings || data.candidate_warnings || []} tone="warn" />
          <MessageList title={t("errors")} items={[...(data.errors || []), data.error || ""].filter(Boolean)} tone="fail" />
        </motion.section>

        <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_340px]">
          <DashboardCard icon={<Workflow size={20} />} title={t("statusMap")} count={format(t("moduleCount"), { count: modules.length })} className="min-h-[620px]">
            <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
              <div className="inline-flex rounded-xl border border-tm-border bg-tm-card-alt p-1">
                {(["overview", "module", "technical"] as const).map((mode) => (
                  <button
                    key={mode}
                    type="button"
                    onClick={() => setView(mode)}
                    className={`rounded-lg px-3 py-1.5 text-sm font-semibold transition-colors ${view === mode ? "bg-tm-accent text-tm-primary" : "text-tm-secondary hover:bg-tm-bg"}`}
                  >
                    {mode === "overview" ? t("overview") : mode === "module" ? t("modules") : t("technical")}
                  </button>
                ))}
              </div>
            </div>
            <AnimatePresence mode="wait">
              {view === "overview" && (
                <motion.div key="overview" initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -10 }}>
                  <ProjectCanvasGraph
                    modules={modules}
                    selectedIndex={selectedIndex}
                    onSelect={(index) => { setSelectedIndex(index); setView("module"); }}
                    hint={t("graphHint")}
                    labels={copy[lang]}
                  />
                </motion.div>
              )}
              {view === "module" && (
                <motion.div key="module" initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -10 }}>
                  <ModuleDetail module={selectedModule} labels={copy[lang]} onBack={() => setView("overview")} />
                </motion.div>
              )}
              {view === "technical" && (
                <motion.div key="technical" initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -10 }}>
                  <TechnicalView source={data.mermaid_src || ""} hint={t("technicalHint")} />
                </motion.div>
              )}
            </AnimatePresence>
          </DashboardCard>

          <aside>
            <DashboardCard icon={<Inbox size={20} />} title={t("candidates")} count={format(t("candidateCount"), { count: candidates.length })}>
              <p className="mb-3 text-xs leading-5 text-tm-tertiary">{t("candidatesIntro")}</p>
              <CandidateShelf candidates={candidates} warnings={data.candidate_warnings || []} labels={copy[lang]} />
            </DashboardCard>

            <DashboardCard icon={<ListChecks size={20} />} title={t("activeModules")}>
              <p className="mb-3 text-sm text-tm-tertiary">{t("activeIntro")}</p>
              <ModuleList modules={modules} selectedIndex={selectedIndex} onSelect={(index) => { setSelectedIndex(index); setView("module"); }} emptyText={t("emptyModules")} labels={copy[lang]} />
            </DashboardCard>
          </aside>
        </div>
      </main>
    </DashboardShell>
  );
}

function InfoBadge({ label, value }: { label: string; value: unknown }) {
  return (
    <div className="min-w-0 rounded-xl border border-tm-border bg-tm-card-alt px-3 py-2">
      <span className="mr-2 font-semibold text-tm-tertiary">{label}</span>
      <code className="break-all text-tm-primary">{text(value)}</code>
    </div>
  );
}

function MessageList({ title, items, tone }: { title: string; items: unknown[]; tone: "warn" | "fail" }) {
  const clean = items.map((item) => text(item, "")).filter(Boolean);
  if (!clean.length) return null;
  const className = tone === "fail" ? "border-tm-fail-border bg-tm-fail-bg text-tm-fail" : "border-tm-warn-border bg-tm-warn-bg text-tm-warn";
  return (
    <div className={`mt-3 rounded-xl border p-3 text-xs leading-5 ${className}`}>
      <div className="mb-1 font-bold">{title}</div>
      {clean.map((item) => <div key={item}>{item}</div>)}
    </div>
  );
}

type GraphTransform = { x: number; y: number; scale: number };
type GraphBox = { width: number; height: number };
type GraphNode = {
  type: "center" | "module";
  index: number;
  module?: CanvasModule;
  kind: Tone | "center";
  x: number;
  y: number;
  titleLines: string[];
  subLines: string[];
  box: GraphBox;
};
type GraphModel = {
  world: { width: number; height: number };
  center: { x: number; y: number };
  radius: number;
  nodes: GraphNode[];
  moduleNodes: GraphNode[];
  bounds: { x: number; y: number; width: number; height: number };
};

const graphWorld = { width: 1680, height: 1080 };

function ProjectCanvasGraph({
  modules,
  selectedIndex,
  onSelect,
  hint,
  labels,
}: {
  modules: CanvasModule[];
  selectedIndex: number;
  onSelect: (index: number) => void;
  hint: string;
  labels: typeof copy.zh;
}) {
  const viewportRef = useRef<HTMLDivElement | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const frameRef = useRef(0);
  const dragRef = useRef({ active: false, moved: false, lastX: 0, lastY: 0 });
  const targetRef = useRef<GraphTransform>({ x: 0, y: 0, scale: 1 });
  const transformRef = useRef<GraphTransform>({ x: 0, y: 0, scale: 1 });
  const [transform, setTransform] = useState<GraphTransform>({ x: 0, y: 0, scale: 1 });
  const [isDragging, setIsDragging] = useState(false);
  const model = useMemo(() => buildGraphModel(modules, labels), [modules, labels]);

  function commitTransform(next: GraphTransform) {
    transformRef.current = next;
    setTransform(next);
  }

  function clampGraphScale(value: number) {
    return Math.min(5, Math.max(0.12, value));
  }

  function setGraphTarget(next: Partial<GraphTransform>) {
    const viewport = viewportRef.current;
    const current = targetRef.current;
    const scale = Number.isFinite(next.scale) ? clampGraphScale(next.scale as number) : current.scale;
    let x = Number.isFinite(next.x) ? (next.x as number) : current.x;
    let y = Number.isFinite(next.y) ? (next.y as number) : current.y;
    if (viewport) {
      const rect = viewport.getBoundingClientRect();
      const padding = 100;
      const minX = padding - (model.bounds.x + model.bounds.width) * scale;
      const maxX = rect.width - padding - model.bounds.x * scale;
      const minY = padding - (model.bounds.y + model.bounds.height) * scale;
      const maxY = rect.height - padding - model.bounds.y * scale;
      x = minX < maxX ? Math.min(maxX, Math.max(minX, x)) : Math.min(minX, Math.max(maxX, x));
      y = minY < maxY ? Math.min(maxY, Math.max(minY, y)) : Math.min(minY, Math.max(maxY, y));
    }
    targetRef.current = { x, y, scale };
    applyGraphTransform();
  }

  function applyGraphTransform() {
    if (frameRef.current) return;
    frameRef.current = window.requestAnimationFrame(() => {
      frameRef.current = 0;
      const target = targetRef.current;
      const current = transformRef.current;
      if (dragRef.current.active) {
        const dx = target.x - current.x;
        const dy = target.y - current.y;
        if (Math.abs(dx) > 0.05 || Math.abs(dy) > 0.05) {
          commitTransform({ x: current.x + dx * 0.65, y: current.y + dy * 0.65, scale: target.scale });
          applyGraphTransform();
          return;
        }
      }
      commitTransform(target);
    });
  }

  function fitGraph() {
    const viewport = viewportRef.current;
    if (!viewport) return;
    const rect = viewport.getBoundingClientRect();
    const rawScale = Math.min((rect.width - 48) / model.bounds.width, (rect.height - 48) / model.bounds.height);
    const minFitScale = rect.width < 560 ? 0.18 : 0.34;
    const scale = clampGraphScale(Math.min(1.08, Math.max(rawScale, minFitScale)));
    targetRef.current = {
      scale,
      x: (rect.width - model.bounds.width * scale) / 2 - model.bounds.x * scale,
      y: (rect.height - model.bounds.height * scale) / 2 - model.bounds.y * scale,
    };
    commitTransform(targetRef.current);
  }

  function zoomGraph(factor: number, clientX?: number, clientY?: number) {
    const viewport = viewportRef.current;
    if (!viewport) return;
    const rect = viewport.getBoundingClientRect();
    const oldScale = targetRef.current.scale || transformRef.current.scale;
    const nextScale = clampGraphScale(oldScale * factor);
    if (Math.abs(nextScale - oldScale) < 0.0001) return;
    const focusX = Number.isFinite(clientX) ? (clientX as number) - rect.left : rect.width / 2;
    const focusY = Number.isFinite(clientY) ? (clientY as number) - rect.top : rect.height / 2;
    const baseX = targetRef.current.x;
    const baseY = targetRef.current.y;
    const localX = (focusX - baseX) / oldScale;
    const localY = (focusY - baseY) / oldScale;
    setGraphTarget({
      scale: nextScale,
      x: focusX - localX * nextScale,
      y: focusY - localY * nextScale,
    });
  }

  useEffect(() => {
    drawGraphCanvas(canvasRef.current, model);
    const id = window.requestAnimationFrame(fitGraph);
    return () => window.cancelAnimationFrame(id);
  }, [model]);

  useEffect(() => {
    const viewport = viewportRef.current;
    if (!viewport || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(() => fitGraph());
    observer.observe(viewport);
    return () => observer.disconnect();
  }, [model]);

  useEffect(() => () => {
    if (frameRef.current) window.cancelAnimationFrame(frameRef.current);
  }, []);

  if (!modules.length) {
    return (
      <div className="rounded-xl border border-tm-border bg-tm-card-alt p-4 text-sm text-tm-secondary">
        {labels.emptyModules}
      </div>
    );
  }

  return (
    <div>
      <div className="mb-3 grid gap-3 rounded-xl border border-tm-border bg-tm-card-alt px-3 py-2 text-xs text-tm-tertiary md:grid-cols-[minmax(0,1fr)_auto] md:items-center">
        <div className="flex min-w-0 items-center gap-2">
          <Search size={14} className="shrink-0 text-tm-accent" />
          <span className="min-w-0 truncate">{hint}</span>
        </div>
        <div className="flex items-center justify-end gap-2">
          <button type="button" onClick={() => zoomGraph(0.84)} className="rounded-lg border border-tm-border bg-tm-card px-2 py-1">{labels.zoomOut}</button>
          <span className="w-12 text-center font-bold">{Math.round(transform.scale * 100)}%</span>
          <button type="button" onClick={() => zoomGraph(1.18)} className="rounded-lg border border-tm-border bg-tm-card px-2 py-1">{labels.zoomIn}</button>
          <button type="button" onClick={fitGraph} className="rounded-lg border border-tm-border bg-tm-card px-2 py-1">适配</button>
          <button type="button" onClick={fitGraph} className="rounded-lg border border-tm-border bg-tm-card px-2 py-1">{labels.reset}</button>
        </div>
      </div>
      <div
        id="canvas-graph-viewport"
        ref={viewportRef}
        className={`relative h-[560px] overflow-hidden rounded-2xl border border-tm-border bg-tm-card-alt ${isDragging ? "cursor-grabbing" : "cursor-grab"}`}
        onWheel={(event) => {
          event.preventDefault();
          const factor = Math.min(1.18, Math.max(0.84, Math.exp(-event.deltaY * 0.00075)));
          zoomGraph(factor, event.clientX, event.clientY);
        }}
        onPointerDown={(event) => {
          if ((event.target as HTMLElement).closest("[data-graph-node], [data-graph-control]")) return;
          dragRef.current = { active: true, moved: false, lastX: event.clientX, lastY: event.clientY };
          setIsDragging(true);
          event.currentTarget.setPointerCapture(event.pointerId);
        }}
        onPointerMove={(event) => {
          if (!dragRef.current.active) return;
          const dx = event.clientX - dragRef.current.lastX;
          const dy = event.clientY - dragRef.current.lastY;
          if (Math.abs(dx) + Math.abs(dy) > 1) dragRef.current.moved = true;
          dragRef.current.lastX = event.clientX;
          dragRef.current.lastY = event.clientY;
          setGraphTarget({ x: targetRef.current.x + dx, y: targetRef.current.y + dy });
        }}
        onPointerUp={(event) => {
          dragRef.current.active = false;
          setIsDragging(false);
          try { event.currentTarget.releasePointerCapture(event.pointerId); } catch {}
        }}
        onPointerCancel={() => {
          dragRef.current.active = false;
          setIsDragging(false);
        }}
      >
        <div
          id="canvas-graph-world"
          className="absolute left-0 top-0 origin-top-left"
          style={{
            width: model.world.width,
            height: model.world.height,
            transform: `translate3d(${transform.x}px, ${transform.y}px, 0) scale(${transform.scale})`,
            transition: isDragging ? "none" : "transform 0.15s cubic-bezier(0.2, 0.8, 0.2, 1)",
          }}
        >
          <canvas
            id="canvas-graph-canvas"
            ref={canvasRef}
            className="absolute inset-0"
            role="img"
            aria-label="项目状态图"
          />
          <div id="canvas-graph-dom-overlay" className="absolute inset-0">
            {model.nodes.map((node) => {
              const selected = node.type === "module" && node.index === selectedIndex;
              return (
                <button
                  key={`${node.type}-${node.index}`}
                  type="button"
                  data-graph-node
                  onClick={() => node.type === "center" ? fitGraph() : onSelect(node.index)}
                  className={`absolute -translate-x-1/2 -translate-y-1/2 rounded-2xl border bg-tm-card text-left shadow-[0_10px_24px_rgba(31,29,27,0.10)] transition-shadow hover:shadow-[0_14px_32px_rgba(168,123,34,0.16)] ${
                    node.type === "center" ? "border-tm-accent p-5 text-center" : selected ? "border-tm-accent p-3" : "border-tm-border p-3"
                  }`}
                  style={{ left: node.x, top: node.y, width: node.box.width, minHeight: node.box.height }}
                >
                  {node.type === "center" && <LayoutDashboard className="mx-auto mb-2 text-tm-accent" size={24} />}
                  <div className="mb-1 flex items-center justify-between gap-2 text-[10px] font-bold uppercase tracking-normal text-tm-tertiary">
                    <span>{node.type === "center" ? "Project Canvas" : "Module"}</span>
                    {node.type === "module" && <span className={`h-2.5 w-2.5 shrink-0 rounded-full ${node.kind === "done" ? "bg-tm-ok" : node.kind === "current" ? "bg-tm-warn" : node.kind === "blocked" ? "bg-tm-fail" : "bg-tm-tertiary"}`} />}
                  </div>
                  <div className={`${node.type === "center" ? "text-sm" : "text-xs"} line-clamp-2 font-extrabold leading-5 text-tm-primary`}>
                    {node.titleLines.join(" ")}
                  </div>
                  <div className={`${node.type === "center" ? "mt-2 text-xs" : "mt-1 text-[11px]"} line-clamp-2 leading-4 text-tm-tertiary`}>
                    {node.subLines.join(" / ")}
                  </div>
                </button>
              );
            })}
          </div>
        </div>
      </div>
    </div>
  );
}

function buildGraphModel(modules: CanvasModule[], labels: typeof copy.zh): GraphModel {
  const world = { ...graphWorld };
  const center = { x: world.width / 2, y: world.height / 2 };
  const visible = modules.slice(0, 32);
  const statusCounts = visible.reduce(
    (acc, item) => {
      const kind = statusTone(item.status);
      if (kind === "done" || kind === "current" || kind === "blocked" || kind === "pending") acc[kind] += 1;
      return acc;
    },
    { done: 0, current: 0, blocked: 0, pending: 0 },
  );
  const centerNode: GraphNode = {
    type: "center",
    index: -1,
    kind: "center",
    x: center.x,
    y: center.y,
    titleLines: ["Project Canvas"],
    subLines: [
      format(labels.moduleCount, { count: visible.length }),
      `${statusCounts.done} done / ${statusCounts.current + statusCounts.blocked} active`,
    ],
    box: { width: 244, height: 122 },
  };
  const moduleNodes = visible.map((item, index) => {
    const ring = Math.floor(index / 18);
    const ringIndex = index % 18;
    const ringSize = Math.min(18, visible.length - ring * 18);
    const angle = -Math.PI / 2 + (Math.PI * 2 * ringIndex) / Math.max(ringSize, 1) + ring * 0.19;
    const radius = 355 + ring * 205;
    const titleLines = labelLines(item.module || "-", 16, 2);
    const cleanStatus = text(item.status, "").replace(/^[✅🟡⚪🔴]\s*/, "").trim();
    const subLines = labelLines(cleanStatus || item.owner || "-", 26, 1);
    return {
      type: "module" as const,
      index,
      module: item,
      kind: statusTone(item.status),
      x: center.x + Math.cos(angle) * radius,
      y: center.y + Math.sin(angle) * radius,
      titleLines,
      subLines,
      box: nodeBox(titleLines, subLines),
    };
  });
  const nodes = [centerNode, ...moduleNodes];
  relaxGraphNodes(nodes);
  const minX = Math.min(...nodes.map((node) => node.x - node.box.width / 2)) - 54;
  const maxX = Math.max(...nodes.map((node) => node.x + node.box.width / 2)) + 54;
  const minY = Math.min(...nodes.map((node) => node.y - node.box.height / 2)) - 54;
  const maxY = Math.max(...nodes.map((node) => node.y + node.box.height / 2)) + 54;
  const shiftX = minX < 0 ? Math.abs(minX) + 50 : 0;
  const shiftY = minY < 0 ? Math.abs(minY) + 50 : 0;
  if (shiftX || shiftY) {
    nodes.forEach((node) => {
      node.x += shiftX;
      node.y += shiftY;
    });
    center.x += shiftX;
    center.y += shiftY;
  }
  world.width = Math.max(graphWorld.width, maxX + shiftX + 50);
  world.height = Math.max(graphWorld.height, maxY + shiftY + 50);
  return { world, center, radius: 355, nodes, moduleNodes, bounds: { x: 0, y: 0, width: world.width, height: world.height } };
}

function textUnits(value: string) {
  return Array.from(value).reduce((total, char) => {
    if (/[\u4e00-\u9fff]/.test(char)) return total + 1.05;
    if (/[A-Z0-9]/.test(char)) return total + 0.72;
    if (/\s/.test(char)) return total + 0.35;
    return total + 0.58;
  }, 0);
}

function labelLines(value: string, max = 18, maxLines = 2) {
  const clean = value.trim();
  if (!clean) return ["-"];
  if (clean.length <= max) return [clean];
  const parts = clean.split(/\s+/).filter(Boolean);
  if (parts.length > 1) {
    const lines: string[] = [];
    let current = "";
    for (const part of parts) {
      const next = current ? `${current} ${part}` : part;
      if (next.length > max && current) {
        lines.push(current);
        current = part;
      } else {
        current = next;
      }
      if (lines.length >= maxLines) break;
    }
    if (current) lines.push(current);
    const clipped = lines.slice(0, maxLines);
    if (clipped.join(" ").length < clean.length && clipped.length) {
      clipped[clipped.length - 1] = `${clipped[clipped.length - 1].slice(0, Math.max(1, max - 1))}...`;
    }
    return clipped;
  }
  const lines: string[] = [];
  for (let index = 0; index < clean.length && lines.length < maxLines; index += max) {
    lines.push(clean.slice(index, index + max));
  }
  if (lines.join("").length < clean.length && lines.length) {
    lines[lines.length - 1] = `${lines[lines.length - 1].slice(0, Math.max(1, max - 1))}...`;
  }
  return lines;
}

function nodeBox(titleLines: string[], subLines: string[]): GraphBox {
  const longestTitle = Math.max(...titleLines.map(textUnits), 1) * 12.5;
  const longestSub = Math.max(...subLines.map(textUnits), 0) * 7;
  const width = Math.min(224, Math.max(166, Math.max(longestTitle, longestSub) + 40));
  const height = Math.max(96, 52 + titleLines.length * 17 + subLines.length * 14);
  return { width, height };
}

function relaxGraphNodes(nodes: GraphNode[]) {
  const spacing = 64;
  const iterations = 96;
  for (let iter = 0; iter < iterations; iter += 1) {
    let moved = false;
    for (let i = 0; i < nodes.length; i += 1) {
      const nodeA = nodes[i];
      for (let j = i + 1; j < nodes.length; j += 1) {
        const nodeB = nodes[j];
        const minDx = nodeA.box.width / 2 + nodeB.box.width / 2 + spacing;
        const minDy = nodeA.box.height / 2 + nodeB.box.height / 2 + spacing;
        let dx = nodeB.x - nodeA.x;
        let dy = nodeB.y - nodeA.y;
        if (dx === 0 && dy === 0) {
          const angle = ((j + 1) * 2.399963229728653) % (Math.PI * 2);
          nodeB.x += Math.cos(angle) * 0.75;
          nodeB.y += Math.sin(angle) * 0.75;
          dx = nodeB.x - nodeA.x;
          dy = nodeB.y - nodeA.y;
        }
        const absDx = Math.abs(dx);
        const absDy = Math.abs(dy);
        if (absDx < minDx && absDy < minDy) {
          const overlapX = minDx - absDx;
          const overlapY = minDy - absDy;
          const pushX = overlapX < overlapY ? overlapX * (dx >= 0 ? 1 : -1) : 0;
          const pushY = overlapX < overlapY ? 0 : overlapY * (dy >= 0 ? 1 : -1);
          if (nodeA.type === "center") {
            nodeB.x += pushX;
            nodeB.y += pushY;
          } else if (nodeB.type === "center") {
            nodeA.x -= pushX;
            nodeA.y -= pushY;
          } else {
            nodeA.x -= pushX * 0.5;
            nodeA.y -= pushY * 0.5;
            nodeB.x += pushX * 0.5;
            nodeB.y += pushY * 0.5;
          }
          moved = true;
        }
      }
    }
    if (!moved) break;
  }
}

function drawGraphCanvas(canvas: HTMLCanvasElement | null, model: GraphModel) {
  if (!canvas) return;
  const dpr = Math.min(2, window.devicePixelRatio || 1);
  const width = model.world.width;
  const height = model.world.height;
  if (canvas.width !== width * dpr || canvas.height !== height * dpr) {
    canvas.width = width * dpr;
    canvas.height = height * dpr;
    canvas.style.width = `${width}px`;
    canvas.style.height = `${height}px`;
  }
  const ctx = canvas.getContext("2d", { alpha: true });
  if (!ctx) return;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, width, height);
  drawGraphHalo(ctx, model.center.x, model.center.y, model.radius);
  drawGraphHalo(ctx, model.center.x, model.center.y, model.radius + 205);
  for (const node of model.moduleNodes) {
    drawGraphCurve(ctx, model.center, node, node.index === 0 || node.index === 1);
  }
}

function drawGraphHalo(ctx: CanvasRenderingContext2D, x: number, y: number, radius: number) {
  ctx.save();
  ctx.beginPath();
  ctx.arc(x, y, radius, 0, Math.PI * 2);
  ctx.setLineDash([8, 12]);
  ctx.lineWidth = 1.2;
  ctx.strokeStyle = "rgba(200, 165, 96, .18)";
  ctx.stroke();
  ctx.restore();
}

function drawGraphCurve(ctx: CanvasRenderingContext2D, from: { x: number; y: number }, to: { x: number; y: number }, strong: boolean) {
  ctx.save();
  ctx.beginPath();
  ctx.moveTo(from.x, from.y);
  ctx.bezierCurveTo(from.x, to.y, to.x, from.y, to.x, to.y);
  ctx.lineWidth = strong ? 2.1 : 1.35;
  ctx.strokeStyle = strong ? "rgba(200, 165, 96, .42)" : "rgba(138, 130, 117, .22)";
  ctx.stroke();
  ctx.restore();
}

function ModuleDetail({ module, labels, onBack }: { module: CanvasModule | null; labels: typeof copy.zh; onBack: () => void }) {
  if (!module) {
    return <div className="rounded-xl border border-tm-border bg-tm-card-alt p-4 text-sm text-tm-secondary">{labels.noSelection}</div>;
  }
  const rows: Array<[string, unknown]> = [
    [labels.moduleStatus, module.status],
    [labels.owner, module.owner],
    [labels.moduleUpdated, module.updated],
  ];
  return (
    <div className="space-y-4">
      <button type="button" onClick={onBack} className="inline-flex items-center gap-2 rounded-xl border border-tm-accent bg-tm-card-alt px-3 py-2 text-xs font-semibold text-tm-secondary">
        <ArrowLeft size={14} />
        {labels.backOverview}
      </button>
      <section className="rounded-2xl border border-tm-border bg-tm-card-alt p-4">
        <div className="flex items-start justify-between gap-3">
          <div>
            <div className="text-xs font-bold text-tm-tertiary">MODULE</div>
            <h3 className="mt-1 text-xl font-extrabold text-tm-primary">{text(module.module)}</h3>
          </div>
          <StatusPill status={module.status} />
        </div>
        <div className="mt-4 grid gap-2 sm:grid-cols-3">
          {rows.map(([label, value]) => (
            <div key={label} className="rounded-xl border border-tm-border bg-tm-bg px-3 py-2">
              <div className="text-xs font-bold text-tm-tertiary">{label}</div>
              <div className="mt-1 break-words text-sm text-tm-primary">{text(value)}</div>
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}

function TechnicalView({ source, hint }: { source: string; hint: string }) {
  if (!source.trim()) {
    return (
      <div className="rounded-xl border border-tm-warn-border bg-tm-warn-bg p-4 text-sm text-tm-warn">
        <AlertTriangle className="mb-2" size={18} />
        No Mermaid source found.
      </div>
    );
  }
  return (
    <div>
      <div className="mb-3 rounded-xl border border-tm-border bg-tm-card-alt px-3 py-2 text-xs text-tm-tertiary">{hint}</div>
      <pre className="max-h-[520px] overflow-auto rounded-2xl border border-tm-border bg-tm-bg p-4 text-xs leading-5 text-tm-secondary">
        <code>{source}</code>
      </pre>
    </div>
  );
}

function CandidateShelf({ candidates, warnings, labels }: { candidates: CanvasCandidate[]; warnings: string[]; labels: typeof copy.zh }) {
  if (!candidates.length && !warnings.length) {
    return <div className="rounded-xl border border-dashed border-tm-border bg-tm-card-alt p-3 text-xs leading-5 text-tm-tertiary">{labels.emptyCandidates}</div>;
  }
  return (
    <div className="space-y-3">
      {warnings.map((warning) => (
        <div key={warning} className="rounded-xl border border-tm-warn-border bg-tm-warn-bg p-3 text-xs leading-5 text-tm-warn">{friendlyCanvasWarning(warning)}</div>
      ))}
      <AnimatePresence>
        {candidates.map((item, index) => {
          const name = item.name || item.target_module || item.summary || "project-canvas";
          return (
            <motion.article key={`${name}-${index}`} layout initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -8 }} className="rounded-xl border border-dashed border-tm-border bg-tm-card-alt p-3">
              <div className="mb-2 flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <div className="break-words text-sm font-semibold leading-5 text-tm-primary">{name}</div>
                  <div className="mt-1 break-words text-xs leading-5 text-tm-secondary">{text(item.summary || item.reason)}</div>
                </div>
                <span className="shrink-0 rounded-full border border-tm-warn-border bg-tm-warn-bg px-2 py-0.5 text-[11px] font-semibold text-tm-warn">{text(item.review_state, "建议纳入")}</span>
              </div>
              <div className="grid gap-1 text-[11px] leading-5 text-tm-tertiary">
                <div><span className="font-semibold text-tm-secondary">{labels.targetModule}：</span>{text(item.target_module, "project-canvas")}</div>
                <div><span className="font-semibold text-tm-secondary">{labels.reason}：</span>{text(item.reason)}</div>
                <div className="flex flex-wrap gap-x-3 gap-y-1">
                  <span>{labels.evidence} {text(item.evidence_count, "0")}</span>
                  <span>{labels.source} {text(item.source || item.candidate_source)}</span>
                  <span>{labels.confidence} {text(item.confidence)}</span>
                </div>
              </div>
            </motion.article>
          );
        })}
      </AnimatePresence>
    </div>
  );
}

function ModuleList({
  modules,
  selectedIndex,
  onSelect,
  emptyText,
  labels,
}: {
  modules: CanvasModule[];
  selectedIndex: number;
  onSelect: (index: number) => void;
  emptyText: string;
  labels: typeof copy.zh;
}) {
  if (!modules.length) return <div className="rounded-xl border border-tm-border bg-tm-card-alt p-3 text-sm text-tm-secondary">{emptyText}</div>;
  return (
    <div className="space-y-2">
      {modules.slice(0, 24).map((item, index) => (
        <button
          key={`${item.module || "module"}-${index}`}
          type="button"
          onClick={() => onSelect(index)}
          className={`w-full rounded-xl border p-3 text-left transition-colors ${selectedIndex === index ? "border-tm-accent bg-tm-warn-bg" : "border-tm-border bg-tm-card-alt hover:border-tm-accent"}`}
        >
          <div className="flex items-start justify-between gap-2">
            <span className="min-w-0 break-words text-sm font-semibold text-tm-primary">{text(item.module)}</span>
            <span
              className={`mt-1 h-2.5 w-2.5 shrink-0 rounded-full ${
                statusTone(item.status) === "done"
                  ? "bg-tm-ok"
                  : statusTone(item.status) === "current"
                    ? "bg-tm-warn"
                    : statusTone(item.status) === "blocked"
                      ? "bg-tm-fail"
                      : "bg-tm-tertiary"
              }`}
              aria-label={text(item.status)}
            />
          </div>
          <div className="mt-2 grid gap-1 text-xs text-tm-tertiary">
            <div>{labels.owner}：{text(item.owner)}</div>
            <div>{labels.moduleUpdated}：{text(item.updated)}</div>
          </div>
        </button>
      ))}
    </div>
  );
}

createRoot(document.getElementById("root")!).render(<App />);
