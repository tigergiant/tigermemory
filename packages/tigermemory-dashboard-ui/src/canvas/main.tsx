import {
  Activity,
  AlertTriangle,
  ArrowLeft,
  Braces,
  CheckCircle2,
  GitBranch,
  Inbox,
  LayoutDashboard,
  ListChecks,
  Loader2,
  RefreshCcw,
  Search,
  Target,
  Workflow,
} from "lucide-react";
import { AnimatePresence, motion } from "motion/react";
import { useEffect, useMemo, useState } from "react";
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
    intro: "渲染 project-canvas.md 的项目星图、活跃模块和待纳入候选。当前为只读视图。",
    steward: "记忆管家",
    loaded: "已加载",
    unavailable: "不可用",
    updated: "更新",
    source: "来源",
    refresh: "刷新",
    refreshing: "正在刷新",
    statusMap: "项目状态图",
    overview: "星图",
    modules: "模块视图",
    technical: "技术图",
    candidates: "待纳入星图",
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
    graphHint: "点击节点查看详情；可用按钮缩放星图。",
    zoomIn: "放大",
    zoomOut: "缩小",
    reset: "复位",
    technicalHint: "React 版先展示 Mermaid 源码作为技术视图；星图总览由同一份数据生成。",
    warnings: "提示",
    errors: "错误",
  },
  en: {
    tagline: "Your AI second brain",
    badge: "Projects",
    title: "Project Canvas",
    intro: "Render project-canvas.md as a read-only star map, active module list, and candidate shelf.",
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
    graphHint: "Click a node to inspect it; use controls to zoom.",
    zoomIn: "Zoom in",
    zoomOut: "Zoom out",
    reset: "Reset",
    technicalHint: "The React view shows Mermaid source here while the star map is generated from the same data.",
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
  const [zoom, setZoom] = useState(1);
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
    <DashboardShell active="/canvas" lang={lang} onToggleLang={toggleLang} tagline={t("tagline")} badge={t("badge")}>
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
              {view === "overview" && (
                <div className="flex items-center gap-2 text-xs text-tm-tertiary">
                  <button type="button" onClick={() => setZoom((current) => Math.max(0.72, current - 0.12))} className="rounded-lg border border-tm-border bg-tm-card-alt px-2 py-1">{t("zoomOut")}</button>
                  <span className="w-12 text-center font-bold">{Math.round(zoom * 100)}%</span>
                  <button type="button" onClick={() => setZoom((current) => Math.min(1.3, current + 0.12))} className="rounded-lg border border-tm-border bg-tm-card-alt px-2 py-1">{t("zoomIn")}</button>
                  <button type="button" onClick={() => setZoom(1)} className="rounded-lg border border-tm-border bg-tm-card-alt px-2 py-1">{t("reset")}</button>
                </div>
              )}
            </div>
            <AnimatePresence mode="wait">
              {view === "overview" && (
                <motion.div key="overview" initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -10 }}>
                  <ProjectStarMap modules={modules} zoom={zoom} selectedIndex={selectedIndex} onSelect={(index) => { setSelectedIndex(index); setView("module"); }} hint={t("graphHint")} />
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

function ProjectStarMap({
  modules,
  zoom,
  selectedIndex,
  onSelect,
  hint,
}: {
  modules: CanvasModule[];
  zoom: number;
  selectedIndex: number;
  onSelect: (index: number) => void;
  hint: string;
}) {
  const visible = modules.slice(0, 18);
  const width = 940;
  const height = 520;
  const center = { x: width / 2, y: height / 2 };
  const radiusX = 340;
  const radiusY = 178;
  const nodes = visible.map((module, index) => {
    const angle = (Math.PI * 2 * index) / Math.max(visible.length, 1) - Math.PI / 2;
    return {
      module,
      x: center.x + Math.cos(angle) * radiusX,
      y: center.y + Math.sin(angle) * radiusY,
      tone: statusTone(module.status),
      index,
    };
  });
  return (
    <div>
      <div className="mb-3 flex items-center gap-2 rounded-xl border border-tm-border bg-tm-card-alt px-3 py-2 text-xs text-tm-tertiary">
        <Search size={14} className="text-tm-accent" />
        {hint}
      </div>
      <div className="relative h-[560px] overflow-hidden rounded-2xl border border-tm-border bg-tm-card-alt">
        <motion.div className="absolute left-1/2 top-1/2 origin-center" animate={{ scale: zoom }} transition={{ type: "spring", stiffness: 260, damping: 30 }} style={{ width, height, marginLeft: -width / 2, marginTop: -height / 2 }}>
          <svg className="absolute inset-0 h-full w-full" viewBox={`0 0 ${width} ${height}`} aria-hidden="true">
            <defs>
              <radialGradient id="canvasCenterGlow" cx="50%" cy="50%" r="55%">
                <stop offset="0%" stopColor="#f6e3a9" stopOpacity="0.7" />
                <stop offset="100%" stopColor="#f6e3a9" stopOpacity="0" />
              </radialGradient>
            </defs>
            <circle cx={center.x} cy={center.y} r="210" fill="url(#canvasCenterGlow)" />
            {nodes.map((node) => (
              <line key={`line-${node.index}`} x1={center.x} y1={center.y} x2={node.x} y2={node.y} stroke="#d8cfba" strokeWidth="1.4" strokeDasharray={node.tone === "pending" ? "5 6" : undefined} />
            ))}
          </svg>
          <div className="absolute left-1/2 top-1/2 w-[210px] -translate-x-1/2 -translate-y-1/2 rounded-2xl border border-tm-accent bg-tm-card p-4 text-center shadow-[0_12px_30px_rgba(168,123,34,0.16)]">
            <LayoutDashboard className="mx-auto mb-2 text-tm-accent" size={24} />
            <div className="text-sm font-extrabold text-tm-primary">Project Canvas</div>
            <div className="mt-1 text-xs text-tm-tertiary">{visible.length} visible modules</div>
          </div>
          {nodes.map((node) => (
            <motion.button
              key={`${node.module.module || "module"}-${node.index}`}
              type="button"
              onClick={() => onSelect(node.index)}
              whileHover={{ y: -3 }}
              className={`absolute w-[180px] -translate-x-1/2 -translate-y-1/2 rounded-xl border bg-tm-card px-3 py-2 text-left shadow-sm transition-shadow hover:shadow-md ${selectedIndex === node.index ? "border-tm-accent" : "border-tm-border"}`}
              style={{ left: node.x, top: node.y }}
            >
              <div className="mb-1 flex items-center justify-between gap-2">
                <span className="truncate text-xs font-bold text-tm-primary">{text(node.module.module)}</span>
                <span className={`h-2 w-2 shrink-0 rounded-full ${node.tone === "done" ? "bg-tm-ok" : node.tone === "current" ? "bg-tm-warn" : node.tone === "blocked" ? "bg-tm-fail" : "bg-tm-tertiary"}`} />
              </div>
              <div className="truncate text-[11px] text-tm-tertiary">{text(node.module.status)}</div>
            </motion.button>
          ))}
        </motion.div>
      </div>
    </div>
  );
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
        <div key={warning} className="rounded-xl border border-tm-warn-border bg-tm-warn-bg p-3 text-xs leading-5 text-tm-warn">{warning}</div>
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
