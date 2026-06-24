import {
  AlertTriangle,
  CircleAlert,
  Database,
  Hourglass,
  Inbox,
  LineChart,
  ListChecks,
  Loader2,
  RefreshCcw,
  Repeat2,
  ShieldCheck,
  Sparkles,
} from "lucide-react";
import { AnimatePresence, motion } from "motion/react";
import { useEffect, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import type { ReactNode } from "react";

import { DashboardCard, DashboardShell } from "../components/DashboardShell";
import "../styles.css";

type Lang = "zh" | "en";
type AnyRecord = Record<string, unknown>;
type Tone = "ok" | "warn" | "fail" | "info";

type SourceItem = {
  label?: string;
  root?: string;
  exists?: boolean;
  event_count?: number;
  tool_calls?: number;
  session_closes?: number;
};

type Proposal = {
  key?: AnyRecord;
  event_type?: string;
  source?: string;
  reason?: string;
  eligible_for_inbox?: boolean;
  repeat_count?: number;
  count?: number;
  confidence?: number;
  recommended_action?: string;
  evidence_refs?: string[];
};

type EvidenceSample = {
  event_type?: string;
  source?: string;
  created_at?: string;
  timestamp?: string;
  summary?: string;
  reason?: string;
  message?: string;
};

type SelfEvolutionData = {
  ok?: boolean;
  loading?: boolean;
  date?: string;
  generated_at?: string;
  mode?: string;
  summary?: {
    event_count?: number;
    counts?: AnyRecord;
    outcome_pending?: number;
    samples?: EvidenceSample[];
  };
  proposal_summary?: {
    total?: number;
    eligible?: number;
    min_repeats?: number;
    min_confidence?: number;
  };
  proposals?: Proposal[];
  baseline?: {
    status?: string;
    counts?: AnyRecord;
    rates?: AnyRecord;
  };
  evidence_sources?: {
    events?: SourceItem[];
    telemetry?: SourceItem[];
    env?: string;
  };
  warnings?: string[];
  errors?: string[];
};

const copy = {
  zh: {
    tagline: "你的 AI 第二大脑",
    badge: "自我进化",
    title: "自我进化",
    intro: "只读查看 hook 证据、重复问题和安全提案；默认只提案，不自动改核心规则。",
    steward: "个人记忆控制台",
    refresh: "手动刷新",
    refreshing: "正在刷新",
    autoLoad: "首次计算会自动从 API 读取",
    mode: "模式",
    updated: "最后更新",
    events: "证据事件",
    eventsSub: "来自 hook 证据日志，只读统计",
    proposals: "重复提案",
    pending: "待回填结果",
    pendingSub: "helped / friction 等结果需后续证据确认",
    baseline: "基线状态",
    sourceEvents: "事件来源",
    sourceTelemetry: "遥测来源",
    unreadable: "路径不可读",
    proposalsTitle: "重复问题提案",
    baselineTitle: "安全基线",
    samplesTitle: "最近证据样本",
    loadingTitle: "正在准备自我进化报告",
    loadingBody: "后端正在读取 hook 证据、重复提案和遥测基线。首次冷启动会慢一些；返回前不会把空壳当作真实数据展示。",
    errorTitle: "自我进化数据暂时没取到",
    emptyProposals: "暂未发现需要提案的重复问题。",
    emptySamples: "暂无可展示的证据样本。",
    noSources: "暂无可读证据来源。",
    suggestedInbox: "建议进 inbox",
    observe: "观察",
    noFrequent: "暂无高频问题",
    calculating: "计算中",
    threshold: "阈值 {count} 次 / 置信 {confidence}",
    repeat: "次数",
    confidence: "置信",
    action: "动作",
    source: "来源",
  },
  en: {
    tagline: "Your AI second brain",
    badge: "Evolution",
    title: "Self-evolution",
    intro: "Read-only view of hook evidence, repeated issues, and safe proposals. It proposes only by default.",
    steward: "Memory steward",
    refresh: "Refresh",
    refreshing: "Refreshing",
    autoLoad: "Initial calculation loads through the API",
    mode: "Mode",
    updated: "Updated",
    events: "Evidence events",
    eventsSub: "Read-only hook evidence count",
    proposals: "Repeat proposals",
    pending: "Pending outcomes",
    pendingSub: "helped / friction outcomes need later confirmation",
    baseline: "Baseline",
    sourceEvents: "Event sources",
    sourceTelemetry: "Telemetry sources",
    unreadable: "Unreadable path",
    proposalsTitle: "Repeated issue proposals",
    baselineTitle: "Safety baseline",
    samplesTitle: "Recent evidence samples",
    loadingTitle: "Preparing self-evolution report",
    loadingBody: "The backend is reading hook evidence, repeated proposals, and telemetry baseline. Cold starts can take a little while.",
    errorTitle: "Self-evolution data is unavailable",
    emptyProposals: "No repeated issue needs a proposal yet.",
    emptySamples: "No evidence samples to show yet.",
    noSources: "No readable evidence sources.",
    suggestedInbox: "Suggest inbox",
    observe: "Observe",
    noFrequent: "No frequent issues",
    calculating: "Calculating",
    threshold: "{count} repeats / {confidence} confidence",
    repeat: "Repeats",
    confidence: "Confidence",
    action: "Action",
    source: "Source",
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

function numberText(value: unknown) {
  if (value === null || value === undefined || value === "") return "0";
  const n = Number(value);
  return Number.isFinite(n) ? n.toLocaleString() : String(value);
}

function format(template: string, values: Record<string, string | number>) {
  return Object.entries(values).reduce((current, [key, value]) => current.replace(`{${key}}`, String(value)), template);
}

function baselineLabel(status: unknown, lang: Lang) {
  const labels: Record<string, { zh: string; en: string }> = {
    ok: { zh: "稳定", en: "Stable" },
    insufficient_tool_calls: { zh: "样本不足", en: "Low sample" },
    insufficient_session_closes: { zh: "收工样本不足", en: "Low close sample" },
    "insufficient_tool_calls,insufficient_session_closes": { zh: "样本不足", en: "Low sample" },
    loading: { zh: "加载中", en: "Loading" },
  };
  const key = text(status, "unknown");
  return labels[key]?.[lang] || key;
}

function toneClass(tone: Tone) {
  if (tone === "ok") return "border-tm-ok-border bg-tm-ok-bg text-tm-ok";
  if (tone === "fail") return "border-tm-fail-border bg-tm-fail-bg text-tm-fail";
  if (tone === "warn") return "border-tm-warn-border bg-tm-warn-bg text-tm-warn";
  return "border-tm-border bg-tm-info-bg text-tm-secondary";
}

function StatusPill({ tone, label }: { tone: Tone; label: string }) {
  return <span className={`rounded-full border px-2.5 py-1 text-xs font-semibold ${toneClass(tone)}`}>{label}</span>;
}

function KpiCard({ icon, label, value, subline, tone }: { icon: ReactNode; label: string; value: string; subline: string; tone: Tone }) {
  return (
    <motion.div
      layout
      className="rounded-xl border border-tm-border bg-tm-card-alt p-4 shadow-[0_1px_2px_rgba(31,29,27,0.03)]"
    >
      <div className="flex items-start justify-between gap-3">
        <div className="rounded-xl border border-tm-border-divider bg-tm-card p-2 text-tm-accent">{icon}</div>
        <StatusPill tone={tone} label={tone === "ok" ? "OK" : tone === "fail" ? "ERR" : tone === "warn" ? "Watch" : "Info"} />
      </div>
      <div className="mt-4 text-xs font-bold text-tm-tertiary">{label}</div>
      <div className="mt-1 text-3xl font-extrabold text-tm-primary">{value}</div>
      <div className="mt-1 min-h-8 text-xs leading-4 text-tm-secondary">{subline}</div>
    </motion.div>
  );
}

function InlineNotice({ messages, loading, lang }: { messages: string[]; loading: boolean; lang: Lang }) {
  const t = copy[lang];
  const shown = loading ? [t.autoLoad, ...messages] : messages;
  if (!shown.length) return null;
  return (
    <div className="mt-4 rounded-xl border border-tm-warn-border bg-tm-warn-bg px-4 py-3 text-sm leading-6 text-tm-warn">
      {shown.join(" · ")}
    </div>
  );
}

function SourceGroup({ title, items, metric, empty }: { title: string; items: SourceItem[]; metric: (item: SourceItem) => string; empty: string }) {
  if (!items.length) {
    return <div className="rounded-xl border border-tm-border bg-tm-card-alt p-4 text-sm text-tm-tertiary">{empty}</div>;
  }
  return (
    <div className="rounded-xl border border-tm-border bg-tm-card-alt p-4">
      <div className="mb-3 flex items-center gap-2 text-sm font-semibold text-tm-primary">
        <Database size={16} className="text-tm-accent" />
        <span>{title}</span>
      </div>
      <div className="space-y-2">
        {items.map((item, index) => (
          <motion.div
            layout
            key={`${item.label || "source"}-${index}`}
            className="rounded-lg border border-tm-border-divider bg-tm-card px-3 py-2"
          >
            <div className="flex flex-wrap items-center justify-between gap-2">
              <b className="text-sm text-tm-primary">{text(item.label, "source")}</b>
              <StatusPill tone={item.exists ? "ok" : "warn"} label={item.exists ? metric(item) : empty} />
            </div>
            <div className="mt-1 break-all text-xs text-tm-tertiary">{text(item.root, "")}</div>
          </motion.div>
        ))}
      </div>
    </div>
  );
}

function ProposalList({ proposals, summary, lang }: { proposals: Proposal[]; summary: SelfEvolutionData["proposal_summary"]; lang: Lang }) {
  const t = copy[lang];
  const eligible = Number(summary?.eligible || 0);
  return (
    <DashboardCard icon={<Inbox size={20} />} title={t.proposalsTitle} count={eligible ? `${eligible}` : t.noFrequent}>
      <AnimatePresence mode="popLayout">
        {proposals.length ? (
          <div className="space-y-3">
            {proposals.map((item, index) => {
              const key = item.key || {};
              const eventType = text(key.event_type || item.event_type, "event");
              const source = text(key.source || item.source, "unknown");
              const confidence = `${Math.round(Number(item.confidence || 0) * 100)}%`;
              const refs = Array.isArray(item.evidence_refs) ? item.evidence_refs.slice(0, 4) : [];
              return (
                <motion.article
                  layout
                  initial={{ opacity: 0, y: 10 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0, y: -8 }}
                  key={`${eventType}-${source}-${index}`}
                  className="rounded-xl border border-tm-border bg-tm-card-alt p-4"
                >
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div className="min-w-0">
                      <div className="break-words text-sm font-bold text-tm-primary">{eventType} · {source}</div>
                      <div className="mt-1 text-xs leading-5 text-tm-tertiary">{text(item.reason, lang === "zh" ? "重复证据达到提案阈值" : "Repeated evidence reached the proposal threshold")}</div>
                    </div>
                    <StatusPill tone={item.eligible_for_inbox ? "warn" : "ok"} label={item.eligible_for_inbox ? t.suggestedInbox : t.observe} />
                  </div>
                  <div className="mt-3 grid gap-2 text-xs text-tm-secondary sm:grid-cols-3">
                    <div className="rounded-lg border border-tm-border-divider bg-tm-card p-2">{t.repeat}: <b>{numberText(item.repeat_count || item.count)}</b></div>
                    <div className="rounded-lg border border-tm-border-divider bg-tm-card p-2">{t.confidence}: <b>{confidence}</b></div>
                    <div className="rounded-lg border border-tm-border-divider bg-tm-card p-2">{t.action}: <b>{text(item.recommended_action, "propose")}</b></div>
                  </div>
                  {refs.length > 0 && (
                    <div className="mt-3 flex flex-wrap gap-2">
                      {refs.map((ref) => (
                        <code key={ref} className="max-w-full break-all rounded-md bg-tm-card px-2 py-1 text-[11px] text-tm-warn">
                          {ref}
                        </code>
                      ))}
                    </div>
                  )}
                </motion.article>
              );
            })}
          </div>
        ) : (
          <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="rounded-xl border border-tm-border bg-tm-card-alt p-4 text-sm text-tm-tertiary">
            {t.emptyProposals}
          </motion.div>
        )}
      </AnimatePresence>
    </DashboardCard>
  );
}

function BaselinePanel({ baseline, lang }: { baseline: SelfEvolutionData["baseline"]; lang: Lang }) {
  const t = copy[lang];
  const counts = baseline?.counts || {};
  const rates = baseline?.rates || {};
  const rows = [
    [lang === "zh" ? "总事件" : "Total events", counts.total_events],
    [lang === "zh" ? "hook 阻塞" : "Hook blocked", counts.hook_blocked],
    [lang === "zh" ? "lesson 检索" : "Lesson searched", counts.lesson_searched],
    [lang === "zh" ? "交接缺失" : "Missing handoff", counts.handoff_missing],
    [lang === "zh" ? "工具调用" : "Tool calls", counts.tool_calls],
    [lang === "zh" ? "会话收工" : "Session closes", counts.session_closes],
    [lang === "zh" ? "阻塞率" : "Block rate", rates.hook_block_rate == null ? null : `${Math.round(Number(rates.hook_block_rate) * 1000) / 10}%`],
    [lang === "zh" ? "交接缺失率" : "Missing rate", rates.handoff_missing_rate == null ? null : `${Math.round(Number(rates.handoff_missing_rate) * 1000) / 10}%`],
  ];
  const stable = baseline?.status === "ok";
  return (
    <DashboardCard
      icon={<LineChart size={20} />}
      title={t.baselineTitle}
      count={baselineLabel(baseline?.status, lang)}
    >
      <div className="mb-3">
        <StatusPill tone={stable ? "ok" : "warn"} label={baselineLabel(baseline?.status, lang)} />
      </div>
      <div className="grid gap-2">
        {rows.map(([label, value]) => (
          <div key={String(label)} className="flex items-center justify-between gap-4 rounded-lg border border-tm-border bg-tm-card-alt px-3 py-2">
            <span className="text-sm text-tm-tertiary">{label}</span>
            <b className="text-sm text-tm-primary">{text(value, lang === "zh" ? "暂无" : "None")}</b>
          </div>
        ))}
      </div>
    </DashboardCard>
  );
}

function SampleGrid({ samples, lang }: { samples: EvidenceSample[]; lang: Lang }) {
  const t = copy[lang];
  return (
    <DashboardCard icon={<ListChecks size={20} />} title={t.samplesTitle} count={`${samples.length}`}>
      {samples.length ? (
        <div className="grid gap-3 md:grid-cols-2">
          {samples.slice(0, 8).map((item, index) => (
            <motion.article
              layout
              key={`${item.event_type || "event"}-${index}`}
              className="rounded-xl border border-tm-border bg-tm-card-alt p-4 text-sm"
            >
              <div className="mb-2 flex items-center justify-between gap-2">
                <b className="min-w-0 break-words text-tm-primary">{text(item.event_type, "event")}</b>
                <span className="shrink-0 rounded-full border border-tm-border-strong bg-tm-card px-2 py-1 text-xs text-tm-secondary">{text(item.source, "unknown")}</span>
              </div>
              <div className="text-xs leading-5 text-tm-tertiary">{text(item.created_at || item.timestamp, "")}</div>
              <div className="mt-2 break-words text-tm-secondary">{text(item.summary || item.reason || item.message, lang === "zh" ? "已记录证据" : "Evidence recorded")}</div>
            </motion.article>
          ))}
        </div>
      ) : (
        <div className="rounded-xl border border-tm-border bg-tm-card-alt p-4 text-sm text-tm-tertiary">{t.emptySamples}</div>
      )}
    </DashboardCard>
  );
}

function App() {
  const initialData = useMemo(() => readJsonScript("tm-self-evolution-data") as SelfEvolutionData, []);
  const [lang, setLang] = useState<Lang>(initialLanguage);
  const [data, setData] = useState<SelfEvolutionData>(initialData);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");
  const t = (key: keyof typeof copy.zh) => copy[lang][key];

  function toggleLang() {
    setLang((current) => {
      const next = current === "zh" ? "en" : "zh";
      window.localStorage.setItem("tm-lang", next);
      return next;
    });
  }

  async function refresh(quiet = false) {
    if (!quiet) setRefreshing(true);
    setError("");
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 45000);
    try {
      const date = data.date || new Date().toISOString().slice(0, 10);
      const res = await fetch(`/api/self-evolution/${encodeURIComponent(date)}`, {
        signal: controller.signal,
        headers: { Accept: "application/json" },
      });
      const next = (await res.json()) as SelfEvolutionData & { error?: string };
      if (next.ok === false) throw new Error(next.error || "self-evolution data unavailable");
      setData(next);
    } catch (err) {
      const message = err instanceof DOMException && err.name === "AbortError"
        ? lang === "zh" ? "首次计算超过 45 秒，已停止等待；可以稍后手动刷新。" : "Initial calculation exceeded 45 seconds. Try refreshing later."
        : err instanceof Error ? err.message : "self-evolution data unavailable";
      setError(message);
    } finally {
      window.clearTimeout(timeout);
      setRefreshing(false);
    }
  }

  useEffect(() => {
    if (data.loading) void refresh(true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const summary = data.summary || {};
  const counts = summary.counts || {};
  const proposalSummary = data.proposal_summary || {};
  const proposals = Array.isArray(data.proposals) ? data.proposals : [];
  const baseline = data.baseline || {};
  const sourceGroups = data.evidence_sources || {};
  const samples = Array.isArray(summary.samples) ? summary.samples : [];
  const warnings = [...(data.warnings || []), ...(data.errors || []), ...(error ? [error] : [])].filter(Boolean);
  const eventCount = Number(summary.event_count || 0);
  const eligible = Number(proposalSummary.eligible || 0);
  const outcomePending = Number(summary.outcome_pending || 0);
  const baselineStatus = baseline.status || "unknown";

  return (
    <DashboardShell active="/self-evolution" lang={lang} onToggleLang={toggleLang} tagline={t("tagline")} badge={t("badge")}>
      <main className="relative z-10 mx-auto max-w-6xl px-6 py-8">
        <motion.section initial={{ opacity: 0, y: 14 }} animate={{ opacity: 1, y: 0 }} className="mb-6 rounded-2xl border border-tm-border bg-tm-card p-5 shadow-[0_1px_2px_rgba(31,29,27,0.04),0_12px_32px_rgba(168,123,34,0.06)]">
          <div className="flex flex-col gap-4 md:flex-row md:items-end md:justify-between">
            <div>
              <div className="mb-2 inline-flex items-center gap-2 text-sm font-medium text-tm-tertiary">
                <Sparkles size={16} className="text-tm-accent" />
                {t("steward")}
              </div>
              <h1 className="text-4xl font-extrabold tracking-normal text-tm-primary">{t("title")}</h1>
              <p className="mt-2 max-w-3xl text-sm leading-6 text-tm-secondary">{t("intro")}</p>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <span className="rounded-full border border-tm-border-strong bg-tm-card-alt px-3 py-1 text-xs text-tm-secondary">
                {t("mode")}: {text(data.mode, "propose_only")}
              </span>
              <span className="rounded-full border border-tm-border-strong bg-tm-card-alt px-3 py-1 text-xs text-tm-secondary">
                {text(data.date)}
              </span>
              <button
                type="button"
                onClick={() => void refresh(false)}
                disabled={refreshing}
                className="inline-flex h-9 items-center gap-2 rounded-xl border border-tm-border bg-tm-card-alt px-3 text-sm font-semibold text-tm-secondary hover:border-tm-accent disabled:opacity-60"
              >
                {refreshing ? <Loader2 size={15} className="animate-spin" /> : <RefreshCcw size={15} />}
                {refreshing ? t("refreshing") : t("refresh")}
              </button>
            </div>
          </div>
          <div className="mt-4 text-sm text-tm-tertiary">
            {t("updated")}: {text(data.generated_at, data.loading ? t("calculating") : "-")}
          </div>
          <InlineNotice messages={warnings} loading={Boolean(data.loading && !error)} lang={lang} />
        </motion.section>

        <AnimatePresence mode="popLayout">
          {data.loading && !eventCount && !error ? (
            <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -8 }} className="mb-6 rounded-2xl border border-tm-border bg-tm-card p-5 text-sm text-tm-secondary">
              <div className="flex items-center gap-2 font-semibold text-tm-primary">
                <Loader2 size={16} className="animate-spin text-tm-accent" />
                {t("loadingTitle")}
              </div>
              <p className="mt-2 leading-6">{t("loadingBody")}</p>
            </motion.div>
          ) : error ? (
            <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -8 }} className="mb-6 rounded-2xl border border-tm-fail-border bg-tm-fail-bg p-5 text-sm text-tm-fail">
              <div className="flex items-center gap-2 font-semibold">
                <CircleAlert size={16} />
                {t("errorTitle")}
              </div>
              <p className="mt-2 leading-6">{error}</p>
            </motion.div>
          ) : null}
        </AnimatePresence>

        <section className="mb-6 grid gap-4 md:grid-cols-2 xl:grid-cols-4">
          <KpiCard icon={<Database size={18} />} label={t("events")} value={numberText(eventCount)} subline={t("eventsSub")} tone={eventCount ? "ok" : "warn"} />
          <KpiCard
            icon={<Repeat2 size={18} />}
            label={t("proposals")}
            value={numberText(eligible)}
            subline={format(t("threshold"), { count: Number(proposalSummary.min_repeats || 3), confidence: Number(proposalSummary.min_confidence || 0.75) })}
            tone={eligible ? "warn" : "ok"}
          />
          <KpiCard icon={<Hourglass size={18} />} label={t("pending")} value={numberText(outcomePending)} subline={t("pendingSub")} tone={outcomePending ? "warn" : "ok"} />
          <KpiCard
            icon={baselineStatus === "ok" ? <ShieldCheck size={18} /> : <AlertTriangle size={18} />}
            label={t("baseline")}
            value={baselineLabel(baselineStatus, lang)}
            subline={`hook ${numberText(counts.hook_blocked)} / handoff ${numberText(counts.handoff_missing)}`}
            tone={baselineStatus === "ok" ? "ok" : "warn"}
          />
        </section>

        <section className="mb-6 grid gap-4 md:grid-cols-2">
          <SourceGroup
            title={t("sourceEvents")}
            items={Array.isArray(sourceGroups.events) ? sourceGroups.events : []}
            metric={(item) => `${numberText(item.event_count)} ${lang === "zh" ? "条事件" : "events"}`}
            empty={t("unreadable")}
          />
          <SourceGroup
            title={t("sourceTelemetry")}
            items={Array.isArray(sourceGroups.telemetry) ? sourceGroups.telemetry : []}
            metric={(item) => `${numberText(item.tool_calls)} tools / ${numberText(item.session_closes)} closes`}
            empty={t("unreadable")}
          />
        </section>

        <section className="grid gap-5 lg:grid-cols-[1.15fr_.85fr]">
          <ProposalList proposals={proposals} summary={proposalSummary} lang={lang} />
          <BaselinePanel baseline={baseline} lang={lang} />
        </section>

        <SampleGrid samples={samples} lang={lang} />
      </main>
    </DashboardShell>
  );
}

const root = createRoot(document.getElementById("root")!);
root.render(<App />);
