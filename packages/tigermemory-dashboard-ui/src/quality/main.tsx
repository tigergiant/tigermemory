import {
  Activity,
  BarChart3,
  Loader2,
  Radar,
  RefreshCcw,
  Route,
  Sparkles,
  TriangleAlert,
} from "lucide-react";
import { AnimatePresence, motion } from "motion/react";
import { useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";

import { DashboardCard, DashboardShell } from "../components/DashboardShell";
import "../styles.css";

type Lang = "zh" | "en";
type AnyRecord = Record<string, unknown>;
type RangeKey = "today" | "7d" | "30d";
type FlowTone = "ok" | "warn" | "info";

type QualityEnvelope = {
  memory?: QualityData;
};

type QualityData = {
  ok?: boolean;
  loading?: boolean;
  date?: string;
  fallback_mode?: boolean;
  counts?: AnyRecord;
  mem0_status?: AnyRecord;
  trace_latency_supported?: boolean;
  trace_summary?: AnyRecord;
  route_flow?: AnyRecord;
  flow?: AnyRecord;
  range?: AnyRecord;
  missing_dates?: string[];
  available_dates?: string[];
  retrieval_release?: AnyRecord;
  warnings?: string[];
  error?: string;
};

const copy = {
  zh: {
    tagline: "你的 AI 第二大脑",
    badge: "记忆质量",
    title: "记忆质量",
    intro: "查看今天的待确认内容、记忆服务连接、回答速度和失败问题；未接入的低频指标不在常规视图里占位。",
    refresh: "刷新质量数据",
    autoRefresh: "自动刷新 45s",
    steward: "记忆管家",
    systemQuality: "记忆系统质量",
    routeFlow: "记忆分流",
    routeSub: "mem0 / Wiki / inbox / discard 四条路线同时展示。",
    statusDistribution: "回答状态分布",
    recommendationQuality: "相关证据推荐质量",
    retrievalRelease: "检索放行状态",
    failures: "P5 真实失败池",
    updating: "正在更新数据；当前数字仍是上一范围，仅作参考。",
    fetchError: "数据暂时没取到，请稍后重试",
    empty: "还没有可用于质量判断的实时写入或回答记录；页面先保留待确认数量和服务连接，等真实写入或审核发生后再展开明细。",
  },
  en: {
    tagline: "Your AI second brain",
    badge: "Quality",
    title: "Memory Quality",
    intro: "Review pending items, memory connectivity, answer latency, and real failures without low-frequency placeholders.",
    refresh: "Refresh quality",
    autoRefresh: "Auto refresh 45s",
    steward: "Memory steward",
    systemQuality: "Memory system quality",
    routeFlow: "Memory routing",
    routeSub: "mem0 / Wiki / inbox / discard routes stay visible together.",
    statusDistribution: "Answer status",
    recommendationQuality: "Recommendation quality",
    retrievalRelease: "Retrieval release",
    failures: "P5 real failures",
    updating: "Refreshing data; current figures still show the previous range.",
    fetchError: "Quality data is temporarily unavailable. Try again later.",
    empty: "No live writes or answer traces are available yet; details will expand after real activity appears.",
  },
} as const;

const rangeFallback: Record<RangeKey, { key: RangeKey; label: string; trace_label: string }> = {
  today: { key: "today", label: "今日", trace_label: "近 24 小时" },
  "7d": { key: "7d", label: "近 7 天", trace_label: "近 7 天" },
  "30d": { key: "30d", label: "近 1 个月", trace_label: "近 30 天" },
};

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

function cx(...items: Array<string | false | null | undefined>) {
  return items.filter(Boolean).join(" ");
}

function text(value: unknown, fallback = "-") {
  if (value === null || value === undefined || value === "") return fallback;
  return String(value);
}

function numeric(value: unknown): number | null {
  if (value === null || value === undefined || value === "") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function numberText(value: unknown, suffix = "") {
  const n = numeric(value);
  if (n === null) return "—";
  return `${n.toLocaleString()}${suffix}`;
}

function isLocalMem0Blocked(value: unknown) {
  const message = text(value, "").toLowerCase();
  return message.includes("local profile") || message.includes("mem0_request blocked");
}

function asRecord(value: unknown): AnyRecord {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as AnyRecord) : {};
}

function asArray(value: unknown): AnyRecord[] {
  return Array.isArray(value) ? (value.filter((item) => item && typeof item === "object") as AnyRecord[]) : [];
}

function qualityRange(memory: QualityData, current: RangeKey) {
  const range = asRecord(memory.range);
  const key = (range.key === "7d" || range.key === "30d" || range.key === "today" ? range.key : current) as RangeKey;
  return { ...rangeFallback[key], ...range, key };
}

function toneClass(tone: "ok" | "warn" | "fail" | "info") {
  if (tone === "ok") return "border-tm-ok-border bg-tm-ok-bg text-tm-ok";
  if (tone === "fail") return "border-tm-fail-border bg-tm-fail-bg text-tm-fail";
  if (tone === "info") return "border-tm-info-border bg-tm-info-bg text-tm-info";
  return "border-tm-warn-border bg-tm-warn-bg text-tm-warn";
}

function StatusPill({ tone, children }: { tone: "ok" | "warn" | "fail" | "info"; children: React.ReactNode }) {
  return <span className={cx("rounded-full border px-2.5 py-1 text-xs font-semibold", toneClass(tone))}>{children}</span>;
}

function MetricCard({ label, value, subline, tone = "info", compact = false }: { label: string; value: unknown; subline: string; tone?: "ok" | "warn" | "fail" | "info"; compact?: boolean }) {
  return (
    <motion.article layout className={cx("rounded-xl border border-tm-border bg-tm-card-alt", compact ? "px-3 py-2.5" : "p-3")}>
      <div className="text-xs font-bold text-tm-tertiary">{label}</div>
      <div className={cx("mt-1 font-extrabold text-tm-primary", compact ? "text-xl leading-6" : "text-2xl")}>{value}</div>
      <div className={cx("mt-1 text-xs", tone === "ok" ? "text-tm-ok" : tone === "fail" ? "text-tm-fail" : tone === "warn" ? "text-tm-warn" : "text-tm-secondary")}>{subline}</div>
    </motion.article>
  );
}

function ProgressBar({ label, value, total, tone = "warn" }: { label: string; value: number; total: number; tone?: "ok" | "warn" | "fail" }) {
  const pct = total > 0 ? Math.round((value * 100) / total) : 0;
  const color = tone === "ok" ? "bg-tm-ok" : tone === "fail" ? "bg-tm-fail" : "bg-tm-accent";
  return (
    <div>
      <div className="mb-2 flex items-center justify-between gap-3 text-sm">
        <span className="font-semibold text-tm-primary">{label}</span>
        <span className="text-tm-secondary">{value} / {pct}%</span>
      </div>
      <div className="h-2 overflow-hidden rounded-full bg-tm-border">
        <motion.div layout className={cx("h-full rounded-full", color)} style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

function RoutePanel({
  memory,
  rangeKey,
  refreshing,
  updatingRange,
  onRangeChange,
}: {
  memory: QualityData;
  rangeKey: RangeKey;
  refreshing: boolean;
  updatingRange: RangeKey | null;
  onRangeChange: (range: RangeKey) => void;
}) {
  const counts = asRecord(memory.counts);
  const flow = asRecord(memory.route_flow || memory.flow);
  const range = qualityRange(memory, rangeKey);
  const outputs = asArray(flow.outputs).filter((slot) => !["issue", "anomaly"].includes(String(slot.key || "").toLowerCase()));
  const sources = asArray(flow.sources);
  const findSlot = (slots: AnyRecord[], keys: string[]) => slots.find((slot) => keys.includes(String(slot.key || "").toLowerCase())) || {};
  const issueFallback = Number(asRecord(memory.trace_summary).status_counts ? (asRecord(asRecord(memory.trace_summary).status_counts).fail || 0) : 0) + Number(asRecord(asRecord(memory.trace_summary).status_counts).error || 0);
  const outputValues = {
    mem0: numeric(findSlot(outputs, ["mem0", "instant"]).value ?? counts.mem0),
    wiki: numeric(findSlot(outputs, ["wiki", "long_term", "long_term_knowledge"]).value ?? counts.wiki),
    inbox: numeric(findSlot(outputs, ["inbox", "manual_review"]).value ?? counts.inbox_today ?? counts.inbox),
    discard: numeric(findSlot(outputs, ["discard"]).value ?? counts.discard),
    issue: numeric(findSlot(asArray(flow.outputs), ["issue", "anomaly"]).value ?? counts.issue ?? issueFallback),
  };
  const sourceValues = {
    daily: numeric(findSlot(sources, ["daily"]).value ?? outputValues.mem0),
    inbox: numeric(findSlot(sources, ["inbox"]).value ?? counts.inbox_today ?? counts.inbox),
    trace: numeric(findSlot(sources, ["trace"]).value ?? counts.trace_count),
  };
  const isLogged = flow.flow_source === "route_events";
  const inputTotal = numeric(flow.input_total ?? flow.today_total) ?? Object.values(sourceValues).reduce((sum, value) => sum + (value ?? 0), 0);
  const sourceRows = isLogged
    ? [
        ["路由流水", inputTotal, "ok" as const],
        ["待审积压", numeric(counts.inbox_pending ?? counts.inbox), "warn" as const],
        ["回答轨迹", sourceValues.trace, "info" as const],
      ]
    : [
        ["即时记忆", sourceValues.daily, "ok" as const],
        ["收件箱", sourceValues.inbox, "warn" as const],
        ["回答轨迹", sourceValues.trace, "info" as const],
      ];
  const outputRows = [
    ["mem0", "即时记忆", isLogged ? "实际写入 Mem0" : "近期事实与偏好", outputValues.mem0, "ok" as const],
    ["wiki", "Wiki 提案", isLogged ? "实际进入候选" : "长期知识候选", outputValues.wiki, "warn" as const],
    ["inbox", "人工审核", isLogged ? "真实退回人工" : "需要确认的内容", outputValues.inbox, "info" as const],
    ["discard", "忽略归档", isLogged ? "实际忽略" : "重复或低价值内容", outputValues.discard, "info" as const],
  ];
  const flowTotal = outputRows.reduce((sum, row) => sum + (row[3] as number | null ?? 0), 0);
  const history = asRecord(flow.history);
  const mem0Status = asRecord(memory.mem0_status);
  const mem0Blocked = isLocalMem0Blocked(mem0Status.error);
  const mem0Ok = mem0Status.ok !== false && outputValues.mem0 !== null;
  const wikiOk = outputValues.wiki !== null;
  const sourceCards = sourceRows.map(([label, value, tone], index) => ({
    key: `source-${index}`,
    label: String(label),
    value: value as number | null,
    total: inputTotal,
    tone: tone as FlowTone,
  }));
  const outputCards = outputRows.map(([key, label, description, value, tone]) => ({
    key: String(key),
    label: String(label),
    description: String(description),
    value: value as number | null,
    total: flowTotal,
    tone: tone as FlowTone,
  }));

  return (
    <DashboardCard icon={<Route size={20} />} title={`${range.label}记忆分流`} count={range.trace_label} className="min-h-[720px]">
      <div className="mb-4 flex flex-col gap-3 xl:flex-row xl:items-start xl:justify-between">
        <div className="min-w-0">
          <p className="text-sm leading-6 text-tm-secondary">
            {Array.isArray(memory.available_dates) ? `已纳入 ${memory.available_dates.length} 天数据` : "实时查看输入、分流规则和输出结果"}
            {Array.isArray(memory.missing_dates) && memory.missing_dates.length ? `，缺 ${memory.missing_dates.length} 天日报` : ""}；mem0 / Wiki / inbox / discard 四条路线同时展示。
          </p>
          <div className="mt-3 flex flex-wrap gap-2">
            <StatusPill tone={mem0Ok ? "ok" : mem0Blocked ? "warn" : "fail"}>Mem0：{mem0Ok ? "已连接" : mem0Blocked ? "local profile 已关闭高级 Mem0" : text(mem0Status.error, "不可达")}</StatusPill>
            <StatusPill tone={wikiOk ? "ok" : "warn"}>Wiki：{wikiOk ? "有写入/提案日志" : "缺写入日志"}</StatusPill>
          </div>
        </div>
        <div className="inline-flex shrink-0 rounded-full border border-tm-border bg-tm-card-alt p-1 text-xs font-semibold text-tm-secondary">
          {(Object.keys(rangeFallback) as RangeKey[]).map((item) => (
            <button
              key={item}
              type="button"
              onClick={() => onRangeChange(item)}
              className={cx("rounded-full px-3 py-1.5 transition-colors", range.key === item ? "bg-tm-accent text-tm-primary shadow-sm" : "text-tm-secondary hover:bg-tm-card")}
              disabled={refreshing && updatingRange === item}
            >
              {rangeFallback[item].label}
            </button>
          ))}
        </div>
      </div>
      {history.note && (
        <div className="mb-3 rounded-xl border border-tm-border bg-tm-card-alt px-3 py-2 text-xs leading-5 text-tm-secondary">
          {text(history.note)}
        </div>
      )}
      <MemoryFlowDiagram sources={sourceCards} outputs={outputCards} />
      <div className="mt-4 grid gap-3 md:grid-cols-4">
        <MetricCard compact label={isLogged ? `${range.label}流水` : `${range.label}候选`} value={numberText(inputTotal)} subline={isLogged ? "已记录路线" : "输入池"} tone="info" />
        <MetricCard compact label="自动处理" value={numberText([outputValues.mem0, outputValues.wiki, outputValues.discard].reduce((sum, value) => sum + (value ?? 0), 0))} subline="即时记忆 / Wiki / 忽略" tone="ok" />
        <MetricCard compact label="人工审核" value={numberText(outputValues.inbox)} subline={isLogged ? "真实退回人工" : "需要确认的内容"} tone="warn" />
        <MetricCard compact label="回答失败" value={numberText(outputValues.issue)} subline="未找到另看状态分布" tone={outputValues.issue ? "fail" : "ok"} />
      </div>
    </DashboardCard>
  );
}

function MemoryFlowDiagram({
  sources,
  outputs,
}: {
  sources: Array<{ key: string; label: string; value: number | null; total: number; tone: FlowTone }>;
  outputs: Array<{ key: string; label: string; description: string; value: number | null; total: number; tone: FlowTone }>;
}) {
  const boardRef = useRef<HTMLDivElement | null>(null);
  const frameRef = useRef(0);
  const [paths, setPaths] = useState<Array<{ id: string; d: string; tone: FlowTone; delay: number }>>([]);
  const [activeFlow, setActiveFlow] = useState<string | null>(null);

  useEffect(() => {
    const board = boardRef.current;
    if (!board) return;

    const draw = () => {
      frameRef.current = 0;
      const rect = board.getBoundingClientRect();
      const engine = board.querySelector<HTMLElement>('[data-flow-id="engine"]');
      if (!engine) return;
      const engineRect = engine.getBoundingClientRect();
      const engineLeft = engineRect.left - rect.left;
      const engineRight = engineRect.right - rect.left;
      const engineCenterY = engineRect.top - rect.top + engineRect.height / 2;

      const next: Array<{ id: string; d: string; tone: FlowTone; delay: number }> = [];
      const pathFor = (x1: number, y1: number, x2: number, y2: number) => {
        const c1x = x1 + (x2 - x1) * 0.48;
        const c2x = x1 + (x2 - x1) * 0.52;
        return `M ${x1} ${y1} C ${c1x} ${y1}, ${c2x} ${y2}, ${x2} ${y2}`;
      };

      board.querySelectorAll<HTMLElement>('[data-flow-id^="source-"]').forEach((node, index) => {
        const nodeRect = node.getBoundingClientRect();
        const flowId = node.dataset.flowId || `source-${index}`;
        next.push({
          id: flowId,
          d: pathFor(nodeRect.right - rect.left, nodeRect.top - rect.top + nodeRect.height / 2, engineLeft + 8, engineCenterY),
          tone: sources[index]?.tone || "info",
          delay: index * 0.16,
        });
      });

      board.querySelectorAll<HTMLElement>('[data-flow-id^="output-"]').forEach((node, index) => {
        const nodeRect = node.getBoundingClientRect();
        const flowId = node.dataset.flowId || `output-${index}`;
        next.push({
          id: flowId,
          d: pathFor(engineRight - 8, engineCenterY, nodeRect.left - rect.left, nodeRect.top - rect.top + nodeRect.height / 2),
          tone: outputs[index]?.tone || "info",
          delay: 0.42 + index * 0.16,
        });
      });
      setPaths(next);
    };

    const schedule = () => {
      if (frameRef.current) return;
      frameRef.current = window.requestAnimationFrame(draw);
    };

    schedule();
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(schedule);
    observer?.observe(board);
    board.querySelectorAll<HTMLElement>("[data-flow-id]").forEach((node) => observer?.observe(node));
    window.addEventListener("resize", schedule);
    return () => {
      window.removeEventListener("resize", schedule);
      observer?.disconnect();
      if (frameRef.current) window.cancelAnimationFrame(frameRef.current);
    };
  }, [sources, outputs]);

  return (
    <div ref={boardRef} className="relative mt-5 min-h-[540px] overflow-hidden rounded-2xl border border-tm-border bg-tm-card-alt p-5">
      <svg className="pointer-events-none absolute inset-0 z-[1] hidden h-full w-full lg:block" aria-hidden="true">
        <defs>
          <filter id="memoryFlowGlow">
            <feGaussianBlur stdDeviation="3" result="blur" />
            <feMerge>
              <feMergeNode in="blur" />
              <feMergeNode in="SourceGraphic" />
            </feMerge>
          </filter>
        </defs>
        {paths.map((path) => <FlowPath key={path.id} id={`flow-${path.id}`} path={path.d} delay={path.delay} tone={path.tone} active={activeFlow === path.id} />)}
      </svg>

      <div className="relative z-10 grid min-h-[500px] gap-4 md:grid-cols-[minmax(150px,0.82fr)_minmax(190px,1fr)_minmax(150px,0.82fr)]">
        <section className="space-y-3">
          <div>
            <div className="text-base font-semibold text-tm-primary">输入池</div>
            <div className="text-xs text-tm-tertiary">写入候选与回答质检来源</div>
          </div>
          {sources.map((source) => (
            <RouteChip key={source.key} flowId={source.key} label={source.label} value={source.value} total={source.total} tone={source.tone} onHover={setActiveFlow} />
          ))}
        </section>

        <section className="flex min-h-[360px] items-center justify-center">
          <motion.div
            data-flow-id="engine"
            onMouseEnter={() => setActiveFlow(null)}
            whileHover={{ scale: 1.03 }}
            className="relative rounded-2xl border border-tm-border-strong bg-tm-card px-7 py-6 text-center shadow-[0_16px_42px_rgba(168,123,34,0.16)]"
          >
            <motion.img
              src="/static/cute_tiger_guard.png"
              alt=""
              className="mx-auto h-32 w-32 object-contain drop-shadow-xl"
              animate={{ y: [0, -4, 0] }}
              transition={{ duration: 3.2, repeat: Infinity, ease: "easeInOut" }}
            />
            <div className="mt-2 text-sm font-bold text-tm-primary">记忆管理虎</div>
            <div className="mt-1 text-[11px] leading-4 text-tm-tertiary">路由、去重、降级与人工回收</div>
          </motion.div>
        </section>

        <section className="space-y-3">
          <div>
            <div className="text-base font-semibold text-tm-primary">输出去向</div>
            <div className="text-xs text-tm-tertiary">四条写入路线同时展示，不把 0 项隐藏</div>
          </div>
          {outputs.map((output) => (
            <RouteChip key={output.key} flowId={`output-${output.key}`} label={output.label} description={output.description} value={output.value} total={output.total} tone={output.tone} onHover={setActiveFlow} />
          ))}
        </section>
      </div>
    </div>
  );
}

function FlowPath({ id, path, delay, tone, active }: { id: string; path: string; delay: number; tone: FlowTone; active: boolean }) {
  const stroke = tone === "ok" ? "#52733a" : tone === "warn" ? "#c8a560" : "#6f8ea0";
  return (
    <>
      <motion.path
        id={id}
        data-flow-path-for={id.replace(/^flow-/, "")}
        d={path}
        fill="none"
        stroke={stroke}
        strokeLinecap="round"
        strokeOpacity={active ? "0.86" : "0.62"}
        strokeWidth={active ? "3.8" : "2.8"}
        filter="url(#memoryFlowGlow)"
        initial={{ pathLength: 0.15 }}
        animate={{ pathLength: [0.15, 1, 0.15] }}
        transition={{ duration: 3.6, delay, repeat: Infinity, ease: "easeInOut" }}
      />
      <circle r={active ? "5" : "4"} fill={stroke} opacity={active ? "0.95" : "0.82"}>
        <animateMotion dur="3.8s" repeatCount="indefinite" begin={`${delay}s`}>
          <mpath href={`#${id}`} />
        </animateMotion>
      </circle>
    </>
  );
}

function RouteChip({
  flowId,
  label,
  description,
  value,
  total,
  tone,
  onHover,
}: {
  flowId: string;
  label: string;
  description?: string;
  value: number | null;
  total: number;
  tone: "ok" | "warn" | "info";
  onHover: (id: string | null) => void;
}) {
  const pct = value !== null && total > 0 ? Math.round((value * 100) / total) : value !== null ? 0 : null;
  return (
    <motion.article
      layout
      data-flow-id={flowId}
      onMouseEnter={() => onHover(flowId)}
      onMouseLeave={() => onHover(null)}
      className={cx("rounded-xl border px-3 py-3 shadow-sm transition-shadow hover:shadow-md", toneClass(tone))}
    >
      <div className="flex items-center justify-between gap-2">
        <div className="min-w-0">
          <div className="text-sm font-semibold">{label}</div>
          {description && <div className="mt-0.5 text-[11px] opacity-80">{description}</div>}
        </div>
        <div className="shrink-0 text-right">
          <div className="text-base font-bold">{value === null ? "缺日志" : numberText(value)}</div>
          <div className="text-[11px] opacity-80">{pct === null ? "--" : `${pct}%`}</div>
        </div>
      </div>
      <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-tm-card/70">
        <motion.div layout className="h-full rounded-full bg-current" style={{ width: `${pct ?? 0}%` }} />
      </div>
    </motion.article>
  );
}

function KpiGrid({ memory, rangeKey }: { memory: QualityData; rangeKey: RangeKey }) {
  const counts = asRecord(memory.counts);
  const range = qualityRange(memory, rangeKey);
  const traceSummary = asRecord(memory.trace_summary);
  const duration = asRecord(traceSummary.duration_ms);
  const statusCounts = asRecord(traceSummary.status_counts);
  const traceTotal = Object.values(statusCounts).reduce((sum, value) => sum + Number(value || 0), 0);
  const answerFailures = Number(statusCounts.error || 0) + Number(statusCounts.fail || 0);
  const notFound = Number(statusCounts.not_found || 0);
  const p95 = numeric(duration.p95);
  const p50 = numeric(duration.p50);
  const hasDuration = p95 !== null && traceTotal > 0;
  const mem0Status = asRecord(memory.mem0_status);
  const mem0Count = numeric(counts.mem0);
  const mem0Blocked = isLocalMem0Blocked(mem0Status.error);
  const mem0Value = mem0Count !== null ? numberText(mem0Count) : numeric(mem0Status.count) !== null ? numberText(mem0Status.count) : mem0Status.ok === false ? (mem0Blocked ? "本地模式" : "不可达") : "未接入";
  const rangeSpan = range.start_date && range.end_date ? `${range.start_date} 至 ${range.end_date}` : text(memory.date, "-");
  const issueValue = traceTotal ? numberText(answerFailures) : "等待记录";

  return (
    <div className="grid gap-3 md:grid-cols-4">
      <MetricCard compact label="待确认内容" value={numberText(counts.inbox ?? 0)} subline={Number(counts.review_hidden || 0) ? `已折叠低价值 ${counts.review_hidden} 项` : `${range.label}审核队列`} tone={Number(counts.inbox || 0) ? "warn" : "ok"} />
      <MetricCard compact label={mem0Count !== null ? `${range.label}进入即时记忆` : "即时记忆连接"} value={mem0Value} subline={mem0Count !== null ? `统计 ${rangeSpan}` : mem0Blocked ? "local profile 下不请求高级 Mem0 HTTP" : mem0Status.ok === false ? text(mem0Status.error, "服务暂时不可连接") : "当前总量待同步"} tone={mem0Status.ok === false ? (mem0Blocked ? "warn" : "fail") : "ok"} />
      <MetricCard compact label="回答耗时 P95" value={hasDuration ? numberText(Math.round(p95 || 0), " ms") : "无记录"} subline={hasDuration ? `P50 ${numberText(Math.round(p50 || 0), " ms")} · 未命中 ${notFound}` : "有回答记录后显示耗时"} tone={hasDuration && (p95 || 0) > 5000 ? "warn" : "ok"} />
      <MetricCard compact label="回答失败" value={issueValue} subline={traceTotal ? (answerFailures ? "模型或链路错误，需要复核" : notFound ? `未命中 ${notFound} 条，属于证据不足拒答` : `${range.trace_label}未见失败项`) : "有回答记录后显示失败项"} tone={answerFailures ? "warn" : "ok"} />
    </div>
  );
}

function StatusSection({ memory, rangeKey }: { memory: QualityData; rangeKey: RangeKey }) {
  const range = qualityRange(memory, rangeKey);
  const counts = asRecord(asRecord(memory.trace_summary).status_counts);
  const rows = [
    ["成功", Number(counts.ok || 0), "ok" as const],
    ["未找到", Number(counts.not_found || 0), "warn" as const],
    ["冲突", Number(counts.conflict || 0), "warn" as const],
    ["错误", Number(counts.error || 0), "fail" as const],
  ];
  const total = rows.reduce((sum, item) => sum + item[1], 0);
  return (
    <DashboardCard icon={<BarChart3 size={20} />} title={`回答状态分布（${range.trace_label}）`}>
      <div className="space-y-4 rounded-2xl border border-tm-border bg-tm-card-alt p-4">
        {total ? rows.filter(([, value]) => value > 0).map(([label, value, tone]) => <ProgressBar key={label as string} label={label as string} value={value as number} total={total} tone={tone as "ok" | "warn" | "fail"} />) : (
          <div className="rounded-xl border border-tm-border bg-tm-card p-4 text-sm leading-6 text-tm-secondary">
            {range.trace_label}回答轨迹还没有可统计状态；有新回答后这里显示成功、未找到、冲突和错误占比。
          </div>
        )}
      </div>
    </DashboardCard>
  );
}

function RecommendationQuality({ memory, rangeKey }: { memory: QualityData; rangeKey: RangeKey }) {
  const range = qualityRange(memory, rangeKey);
  const trace = asRecord(memory.trace_summary);
  const quality = asRecord(trace.recommendation_quality);
  const feedback = asRecord(quality.feedback_summary);
  const actionCounts = asRecord(feedback.action_counts);
  const topNoisy = asArray(quality.top_noisy_reasons);
  const metrics = [
    ["sidecar已展示", quality.recommendation_shown_count || 0, `${range.trace_label}有 sidecar 候选`],
    ["related 推荐", quality.recommendation_candidate_count || 0, "相关证据候选"],
    ["boost 尝试", quality.recommendation_boost_attempted_count || 0, "已评估回答行"],
    ["已用于证据", quality.recommendation_used_as_evidence_count || 0, "通过证据门禁后接入回答"],
    ["被门禁拦截", quality.recommendation_blocked_by_gate_count || 0, "仅作降噪信号"],
  ];
  const hasData = Number(trace.row_count || 0) > 0 || metrics.some(([, value]) => Number(value || 0) > 0) || topNoisy.length > 0 || Object.keys(actionCounts).length > 0;

  return (
    <DashboardCard icon={<Sparkles size={20} />} title="相关证据推荐质量">
      {hasData ? (
        <div className="space-y-3">
          <div className="grid gap-2 md:grid-cols-2">
            {metrics.map(([label, value, subline]) => <MetricCard key={String(label)} label={String(label)} value={numberText(value)} subline={String(subline)} tone={Number(value || 0) ? "warn" : "ok"} />)}
          </div>
          {Object.keys(actionCounts).length > 0 && (
            <div className="rounded-xl border border-tm-border bg-tm-card-alt p-3">
              <div className="text-sm font-semibold text-tm-primary">显式反馈</div>
              <div className="mt-2 grid gap-2 md:grid-cols-3">
                {["clicked", "ignored", "selected"].map((key) => <MetricCard key={key} label={key} value={numberText(actionCounts[key] || 0)} subline="人工反馈" tone={Number(actionCounts[key] || 0) ? "warn" : "ok"} />)}
              </div>
            </div>
          )}
          <div className="rounded-xl border border-tm-border bg-tm-card-alt p-3">
            <div className="text-sm font-semibold text-tm-primary">高频拦截原因</div>
            <ul className="mt-2 space-y-1">
              {topNoisy.length ? topNoisy.map((row, index) => <li key={`${row.reason_category}-${index}`} className="rounded-md border border-tm-border bg-tm-card px-2 py-1.5 text-xs">{text(row.reason_category)} × {numberText(row.count || 0)}</li>) : <li className="rounded-md border border-tm-border bg-tm-card px-2 py-1.5 text-xs text-tm-tertiary">最近未出现门禁拦截噪音。</li>}
            </ul>
          </div>
        </div>
      ) : (
        <div className="rounded-xl border border-tm-border bg-tm-card-alt p-4 text-sm leading-6 text-tm-secondary">
          {range.trace_label}暂无可聚合推荐指标；有新回答后展示侧栏建议与门禁分布。
        </div>
      )}
    </DashboardCard>
  );
}

function RetrievalRelease({ memory }: { memory: QualityData }) {
  const payload = asRecord(memory.retrieval_release);
  if (!payload.schema_version) return null;
  const latest = asRecord(payload.latest);
  const production = asRecord(latest.production);
  const mapArm = asRecord(latest.map_arm);
  const deltas = asRecord(payload.deltas);
  const flags = asRecord(asRecord(payload.flags).flags);
  const enabled = Boolean(payload.default_enabled);
  const decisionTone = enabled || payload.decision === "service_default_enabled" || payload.decision === "default_candidate" ? "ok" : "warn";
  const artifactName = (row: AnyRecord) => text(row.artifact, "未记录").split(/[\\/]/).slice(-2).join("/");
  const ratio = (row: AnyRecord, key: string) => {
    const value = numeric(row[key]);
    const total = numeric(row.expected_path_case_count);
    if (value === null) return "缺数据";
    return total && total > 0 ? `${numberText(value)} / ${numberText(total)}` : numberText(value);
  };
  return (
    <DashboardCard icon={<Radar size={20} />} title="检索放行状态">
      <div className="space-y-3">
        <div className="rounded-xl border border-tm-border bg-tm-card-alt p-3">
          <div className="flex flex-wrap items-start justify-between gap-2">
            <div>
              <div className="text-sm font-semibold text-tm-primary">{text(payload.decision, "待判断")}</div>
              <div className={cx("mt-1 text-xs leading-5", decisionTone === "ok" ? "text-tm-ok" : "text-tm-warn")}>{text(payload.summary, "等待下一次 holdout 证据。")}</div>
            </div>
            <StatusPill tone={decisionTone}>{enabled ? "运行中" : "未默认"}</StatusPill>
          </div>
          <div className="mt-3 flex flex-wrap gap-2">
            {[
              ["map arm", enabled ? "已启用" : "未启用"],
              ["summary", flags.TM_EMBED_SUMMARY_WEIGHT || "0"],
              ["bridge", flags.TM_ANSWER_WIKI_MAP_BRIDGE || "0"],
              ["旧 map", flags.TM_ANSWER_WIKI_MAP || "0"],
            ].map(([label, value]) => <StatusPill key={String(label)} tone="info">{label}：{text(value)}</StatusPill>)}
          </div>
        </div>
        <div className="grid gap-2 md:grid-cols-2">
          <MetricCard label="production 证据命中" value={ratio(production, "answer_evidence_hit")} subline={artifactName(production)} tone="info" />
          <MetricCard label="map arm 证据命中" value={ratio(mapArm, "answer_evidence_hit")} subline={artifactName(mapArm)} tone="info" />
          <MetricCard label="证据命中变化" value={numeric(deltas.answer_evidence_hit) === null ? "缺数据" : `${Number(deltas.answer_evidence_hit) > 0 ? "+" : ""}${numberText(deltas.answer_evidence_hit)}`} subline="answer evidence hit" tone={Number(deltas.answer_evidence_hit || 0) > 0 ? "ok" : "warn"} />
          <MetricCard label="漏点变化" value={numeric(deltas.map_hit_but_evidence_miss) === null ? "缺数据" : `${Number(deltas.map_hit_but_evidence_miss) > 0 ? "+" : ""}${numberText(deltas.map_hit_but_evidence_miss)}`} subline="map hit but evidence miss" tone={Number(deltas.map_hit_but_evidence_miss || 0) < 0 ? "ok" : "warn"} />
        </div>
        <div className="rounded-xl border border-tm-border bg-tm-card-alt p-3 text-xs leading-5 text-tm-secondary">
          <div className="font-semibold text-tm-primary">回退方式</div>
          <div className="mt-1">{text(payload.rollback, "关闭 TM_HYBRID_MAP_ARM 后重启服务。")}</div>
        </div>
      </div>
    </DashboardCard>
  );
}

function Failures({ memory, rangeKey }: { memory: QualityData; rangeKey: RangeKey }) {
  const range = qualityRange(memory, rangeKey);
  const trace = asRecord(memory.trace_summary);
  const intake = asRecord(trace.real_failure_intake);
  const statusCounts = asRecord(trace.status_counts);
  const traceTotal = Object.values(statusCounts).reduce((sum, value) => sum + Number(value || 0), 0);
  const latest = (asArray(intake.latest).length ? asArray(intake.latest) : asArray(trace.latest).filter((item) => item.status && item.status !== "ok")).slice(0, 6);
  const candidateCount = Number(intake.candidate_count || latest.length || 0);
  if (!traceTotal) {
    return (
      <DashboardCard icon={<TriangleAlert size={20} />} title="P5 真实失败池">
        <div className="rounded-xl border border-tm-border bg-tm-card-alt p-4 text-sm leading-6 text-tm-secondary">P5 真实失败池暂无样本；有未找到、冲突、错误或失败记录后会进入这里。</div>
      </DashboardCard>
    );
  }
  return (
    <DashboardCard icon={<TriangleAlert size={20} />} title="P5 真实失败池" count={`${candidateCount}`}>
      {latest.length ? (
        <div className="space-y-3">
          <div className="rounded-2xl border border-tm-border bg-tm-card-alt p-4 text-sm leading-6 text-tm-secondary">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <span className="font-semibold text-tm-primary">真实失败候选 {numberText(candidateCount)}</span>
              <span className="text-xs text-tm-tertiary">{range.trace_label}</span>
            </div>
          </div>
          <AnimatePresence>
            {latest.map((item, index) => (
              <motion.article key={`${item.trace_id || index}`} layout initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -8 }} className="rounded-2xl border border-tm-border bg-tm-card-alt p-4">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="flex items-center gap-2">
                    <StatusPill tone={item.status === "error" ? "fail" : "warn"}>{text(item.status, "warn")}</StatusPill>
                    <code className="text-xs text-tm-secondary">{text(item.trace_id, "未记录")}</code>
                  </div>
                  <span className="text-xs text-tm-tertiary">{text(item.ts, "")}</span>
                </div>
                <div className="mt-2 grid gap-1 text-xs text-tm-secondary md:grid-cols-3">
                  <div>问题类别 <code>{text(item.query_class, "未记录")}</code></div>
                  <div>耗时 <code>{numeric(item.duration_ms) !== null ? `${text(item.duration_ms)} ms` : "未记录"}</code></div>
                  <div>模型 <code>{text(item.llm, "未记录")}</code></div>
                </div>
                <div className="mt-2 text-xs text-tm-tertiary">query hash <code>{text(item.query_hash, "未记录")}</code> · 来源 <code>{text(item.source_kind, "未记录")}</code></div>
              </motion.article>
            ))}
          </AnimatePresence>
        </div>
      ) : (
        <div className="rounded-2xl border border-tm-ok-border bg-tm-ok-bg p-4 text-sm text-tm-ok">{range.trace_label}未发现新的 P5 真实失败候选。</div>
      )}
    </DashboardCard>
  );
}

function QualitySignalsPanel({ memory, rangeKey }: { memory: QualityData; rangeKey: RangeKey }) {
  const range = qualityRange(memory, rangeKey);
  const trace = asRecord(memory.trace_summary);
  const statusCounts = asRecord(trace.status_counts);
  const statusRows = [
    ["成功", Number(statusCounts.ok || 0), "ok" as const],
    ["未找到", Number(statusCounts.not_found || 0), "warn" as const],
    ["冲突", Number(statusCounts.conflict || 0), "warn" as const],
    ["错误", Number(statusCounts.error || 0), "fail" as const],
  ];
  const statusTotal = statusRows.reduce((sum, row) => sum + row[1], 0);
  const quality = asRecord(trace.recommendation_quality);
  const topNoisy = asArray(quality.top_noisy_reasons).slice(0, 3);
  const feedback = asRecord(quality.feedback_summary);
  const actionCounts = asRecord(feedback.action_counts);
  const intake = asRecord(trace.real_failure_intake);
  const latestFailures = (asArray(intake.latest).length ? asArray(intake.latest) : asArray(trace.latest).filter((item) => item.status && item.status !== "ok")).slice(0, 3);
  const candidateCount = Number(intake.candidate_count || latestFailures.length || 0);
  const release = asRecord(memory.retrieval_release);
  const enabled = Boolean(release.default_enabled) || release.decision === "service_default_enabled" || release.decision === "default_candidate";
  const hasRecommendation = Number(quality.recommendation_shown_count || 0) > 0 || Number(quality.recommendation_candidate_count || 0) > 0 || topNoisy.length > 0;

  return (
    <DashboardCard icon={<Radar size={20} />} title="记忆分类与质量信号" count={range.trace_label} className="xl:sticky xl:top-24">
      <div className="space-y-4">
        <section className="rounded-2xl border border-tm-border bg-tm-card-alt p-4">
          <div className="flex items-center justify-between gap-3">
            <div>
              <div className="text-sm font-semibold text-tm-primary">回答分类</div>
              <div className="mt-1 text-xs text-tm-tertiary">成功、拒答和异常合并展示，避免重复占用卡片。</div>
            </div>
            <StatusPill tone={statusTotal ? "ok" : "warn"}>{statusTotal ? `${statusTotal} 条` : "等待记录"}</StatusPill>
          </div>
          <div className="mt-4 space-y-3">
            {statusTotal ? statusRows.filter(([, value]) => value > 0).map(([label, value, tone]) => (
              <ProgressBar key={label as string} label={label as string} value={value as number} total={statusTotal} tone={tone as "ok" | "warn" | "fail"} />
            )) : (
              <div className="rounded-xl border border-tm-border bg-tm-card p-3 text-xs leading-5 text-tm-tertiary">有回答记录后显示成功、未找到、冲突和错误占比。</div>
            )}
          </div>
        </section>

        <section className="rounded-2xl border border-tm-border bg-tm-card-alt p-4">
          <div className="mb-3 flex items-center justify-between gap-3">
            <div>
              <div className="text-sm font-semibold text-tm-primary">相关证据推荐质量</div>
              <div className="mt-1 text-xs text-tm-tertiary">把 sidecar、门禁和人工反馈压缩在同一面板。</div>
            </div>
            <StatusPill tone={hasRecommendation ? "warn" : "ok"}>{hasRecommendation ? "有信号" : "暂无噪音"}</StatusPill>
          </div>
          <div className="grid grid-cols-3 gap-2">
            {[
              ["展示", quality.recommendation_shown_count || 0],
              ["采用", quality.recommendation_used_as_evidence_count || 0],
              ["拦截", quality.recommendation_blocked_by_gate_count || 0],
            ].map(([label, value]) => (
              <div key={String(label)} className="rounded-xl border border-tm-border bg-tm-card px-2 py-2 text-center">
                <div className="text-[11px] text-tm-tertiary">{label}</div>
                <div className="mt-1 text-lg font-extrabold text-tm-primary">{numberText(value)}</div>
              </div>
            ))}
          </div>
          <div className="mt-3 grid gap-2 text-xs">
            {topNoisy.length ? topNoisy.map((row, index) => (
              <div key={`${row.reason_category}-${index}`} className="rounded-lg border border-tm-border bg-tm-card px-2 py-1.5 text-tm-secondary">{text(row.reason_category)} × {numberText(row.count || 0)}</div>
            )) : (
              <div className="rounded-lg border border-tm-border bg-tm-card px-2 py-1.5 text-tm-tertiary">最近未出现门禁拦截噪音。</div>
            )}
            {Object.keys(actionCounts).length > 0 && (
              <div className="rounded-lg border border-tm-border bg-tm-card px-2 py-1.5 text-tm-secondary">
                显式反馈：clicked {numberText(actionCounts.clicked || 0)} / ignored {numberText(actionCounts.ignored || 0)} / selected {numberText(actionCounts.selected || 0)}
              </div>
            )}
          </div>
        </section>

        <section className="rounded-2xl border border-tm-border bg-tm-card-alt p-4">
          <div className="flex items-center justify-between gap-3">
            <div>
              <div className="text-sm font-semibold text-tm-primary">P5 真实失败池</div>
              <div className="mt-1 text-xs text-tm-tertiary">{candidateCount ? "仅展示 trace/hash 摘要，不展开原始 query。" : "P5 真实失败池暂无样本。"}</div>
            </div>
            <StatusPill tone={candidateCount ? "warn" : "ok"}>{numberText(candidateCount)}</StatusPill>
          </div>
          <div className="mt-3 space-y-2">
            {latestFailures.length ? latestFailures.map((item, index) => (
              <div key={`${item.trace_id || index}`} className="rounded-xl border border-tm-border bg-tm-card px-3 py-2 text-xs leading-5 text-tm-secondary">
                <div className="flex items-center justify-between gap-2">
                  <span className="font-semibold text-tm-primary">{text(item.status, "warn")}</span>
                  <code>{text(item.trace_id, "未记录")}</code>
                </div>
                <div className="mt-1 text-tm-tertiary">query hash {text(item.query_hash, "未记录")} · {numeric(item.duration_ms) !== null ? `${text(item.duration_ms)} ms` : "耗时未记录"}</div>
              </div>
            )) : (
              <div className="rounded-xl border border-tm-border bg-tm-card px-3 py-2 text-xs text-tm-tertiary">有未找到、冲突、错误或失败记录后会进入这里。</div>
            )}
          </div>
        </section>

        {release.schema_version && (
          <section className="rounded-2xl border border-tm-border bg-tm-card-alt p-4">
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <div className="text-sm font-semibold text-tm-primary">检索放行状态</div>
                <div className="mt-1 line-clamp-3 text-xs leading-5 text-tm-secondary">{text(release.summary, "等待下一次 holdout 证据。")}</div>
              </div>
              <StatusPill tone={enabled ? "ok" : "warn"}>{enabled ? "运行中" : "未默认"}</StatusPill>
            </div>
          </section>
        )}
      </div>
    </DashboardCard>
  );
}

function App() {
  const initialData = useMemo(() => readJsonScript("tm-quality-data") as QualityEnvelope, []);
  const initialMemory = initialData.memory || (initialData as QualityData);
  const [lang, setLang] = useState<Lang>(initialLanguage);
  const [memory, setMemory] = useState<QualityData>(initialMemory || {});
  const [rangeKey, setRangeKey] = useState<RangeKey>(() => qualityRange(initialMemory || {}, "today").key);
  const [refreshing, setRefreshing] = useState(false);
  const [updatingRange, setUpdatingRange] = useState<RangeKey | null>(null);
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const requestIdRef = useRef(0);
  const t = (key: keyof typeof copy.zh) => copy[lang][key];

  function toggleLang() {
    setLang((current) => {
      const next = current === "zh" ? "en" : "zh";
      window.localStorage.setItem("tm-lang", next);
      return next;
    });
  }

  async function fetchQuality(nextRange = rangeKey, quiet = false) {
    abortRef.current?.abort();
    const requestId = requestIdRef.current + 1;
    requestIdRef.current = requestId;
    const controller = new AbortController();
    abortRef.current = controller;
    if (!quiet) setRefreshing(true);
    setUpdatingRange(nextRange);
    setError(null);
    try {
      const params = new URLSearchParams({ range: nextRange });
      const response = await fetch(`/api/quality/memory?${params.toString()}`, { signal: controller.signal });
      if (requestId !== requestIdRef.current) return;
      const payload = (await response.json()) as QualityData;
      setMemory(payload);
      setRangeKey(qualityRange(payload, nextRange).key);
    } catch (exc) {
      if ((exc as Error).name !== "AbortError") setError(t("fetchError"));
    } finally {
      if (requestId === requestIdRef.current) {
        setRefreshing(false);
        setUpdatingRange(null);
      }
    }
  }

  useEffect(() => {
    if (memory.loading) fetchQuality(rangeKey, true);
    const id = window.setInterval(() => {
      if (!document.hidden) fetchQuality(rangeKey, true);
    }, 45000);
    return () => {
      window.clearInterval(id);
      abortRef.current?.abort();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rangeKey]);

  const range = qualityRange(memory, rangeKey);
  const traceCounts = asRecord(asRecord(memory.trace_summary).status_counts);
  const traceTotal = Object.values(traceCounts).reduce((sum, value) => sum + Number(value || 0), 0);
  const hasTrace = Boolean(memory.trace_latency_supported && traceTotal > 0);
  const empty = Boolean(memory.fallback_mode && numeric(asRecord(memory.counts).mem0) === null && Number(asRecord(memory.counts).inbox || 0) <= 0 && traceTotal <= 0);

  return (
    <DashboardShell active="/quality" lang={lang} onToggleLang={toggleLang} tagline={t("tagline")} badge={t("badge")}>
      <main className="relative z-10 mx-auto max-w-6xl px-5 py-6">
        <DashboardCard>
          <div className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
            <div className="min-w-0">
              <div className="mb-2 inline-flex items-center gap-2 text-sm font-medium text-tm-tertiary">
                <Activity size={16} className="text-tm-accent" />
                <span>{t("steward")}</span>
              </div>
              <h1 className="text-2xl font-extrabold leading-9 text-tm-primary">{t("title")}</h1>
              <p className="mt-2 max-w-3xl text-sm leading-6 text-tm-secondary">{t("intro")}</p>
            </div>
            <div className="flex flex-wrap items-center gap-3">
              <span className="rounded-full border border-tm-border-strong bg-tm-card-alt px-3 py-1 text-xs text-tm-secondary">{t("autoRefresh")}</span>
              <button type="button" onClick={() => fetchQuality(rangeKey, false)} className="inline-flex items-center gap-2 rounded-md bg-tm-accent px-4 py-2 text-sm font-semibold text-tm-primary hover:bg-tm-accent-hi disabled:opacity-50" disabled={refreshing}>
                {refreshing ? <Loader2 size={16} className="animate-spin" /> : <RefreshCcw size={16} />}
                {t("refresh")}
              </button>
            </div>
          </div>
        </DashboardCard>

        <AnimatePresence>
          {(updatingRange || memory.fallback_mode || error || memory.error) && (
            <motion.section initial={{ opacity: 0, y: -6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -6 }} className={cx("mb-4 rounded-2xl border p-4 text-sm leading-6", error || memory.error ? "border-tm-fail-border bg-tm-fail-bg text-tm-fail" : "border-tm-warn-border bg-tm-warn-bg text-tm-warn")}>
              {error || memory.error ? text(error || memory.error) : updatingRange ? t("updating").replace("数据", `${rangeFallback[updatingRange].label}数据`) : `${range.label}实时模式：当前直接读取 Mem0、收件箱、回答轨迹和 discard 审计；日报只作为历史快照。`}
            </motion.section>
          )}
        </AnimatePresence>

        <section className="mt-5 grid gap-5 xl:grid-cols-[minmax(0,1.68fr)_360px]">
          <div className="min-w-0">
            {empty && <div className="mb-5 rounded-2xl border border-tm-warn-border bg-tm-warn-bg p-4 text-sm leading-6 text-tm-warn">{range.label}{t("empty")}</div>}
            <RoutePanel
              memory={memory}
              rangeKey={range.key}
              refreshing={refreshing}
              updatingRange={updatingRange}
              onRangeChange={(item) => {
                setRangeKey(item);
                fetchQuality(item, false);
              }}
            />
          </div>

          <aside className="min-w-0">
            <QualitySignalsPanel memory={memory} rangeKey={range.key} />
          </aside>
        </section>
      </main>
    </DashboardShell>
  );
}

createRoot(document.getElementById("root")!).render(<App />);
