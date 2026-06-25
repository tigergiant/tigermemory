import {
  Activity,
  AlertCircle,
  Bot,
  BrainCircuit,
  CheckCircle2,
  CircleDot,
  ClipboardCheck,
  GitCommit,
  Loader2,
  Play,
  RefreshCcw,
  SearchCheck,
  ShieldCheck,
  TerminalSquare,
  XCircle,
} from "lucide-react";
import { AnimatePresence, motion } from "motion/react";
import { useEffect, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";

import { DashboardCard, DashboardShell } from "../components/DashboardShell";
import "../styles.css";

type Lang = "zh" | "en";
type AnyRecord = Record<string, unknown>;
type Tone = "ok" | "warn" | "fail" | "info";

type AgentStatus = {
  ok?: boolean;
  os?: string;
  cursor?: ConnectionInfo;
  claude?: ConnectionInfo;
  warning?: string;
};

type ConnectionInfo = {
  exists?: boolean;
  connected?: boolean;
  path?: string;
  error?: string;
};

type ActivityItem = {
  type?: string;
  agent?: string;
  title?: string;
  created_at?: string;
  sha?: string;
};

type DoctorCheck = {
  name?: string;
  status?: string;
  ok?: boolean;
  reason?: string;
  error?: string;
  branch?: string;
  ahead?: number;
  behind?: number;
  hit_count?: number;
};

type DoctorReport = {
  status?: string;
  recommended_action?: string;
  checks?: DoctorCheck[];
};

type EvalResult = {
  id?: string;
  description?: string;
  wiki_rank?: number;
  wiki_latency_ms?: number;
  wiki_degraded?: boolean;
  mem0_match?: boolean;
  mem0_latency_ms?: number;
  mem0_status?: string;
};

type EvalPayload = {
  ok?: boolean;
  total_cases?: number;
  results?: EvalResult[];
  wiki?: { recall_1?: number; recall_3?: number; recall_5?: number; avg_latency_ms?: number };
  mem0?: { active?: boolean; accuracy?: number; avg_latency_ms?: number };
  error?: string;
  hint?: string;
};

const copy = {
  zh: {
    tagline: "你的 AI 第二大脑",
    badge: "AI 连接",
    title: "AI 连接与检查",
    intro: "检查 Claude / Cursor 是否连上 TigerMemory，并做只读诊断和搜索召回测试。",
    readOnly: "只读模式",
    readOnlyHint: "这些操作不会修改文件或配置。",
    recent: "最近活跃",
    connections: "连接状态",
    doctor: "工作区健康诊断",
    eval: "搜索召回测试",
    refresh: "刷新状态",
    runDoctor: "开始体检",
    rerunDoctor: "重新运行健康诊断",
    runEval: "运行搜索测试",
    rerunEval: "重新运行搜索测试",
    loading: "正在加载...",
    emptyActivity: "暂无最近活跃记录。",
    unavailable: "暂时无法读取",
    connected: "已连接",
    disconnected: "未连接",
    notFound: "未发现",
    path: "检测路径",
    cursor: "Cursor",
    claude: "Claude Desktop",
    os: "运行系统",
    command: "安全命令",
    doctorAdvice: "运维建议",
    status: "状态",
    ready: "可用",
    blocked: "有阻塞",
    total: "共",
    activities: "条",
    wikiRecall1: "Wiki Recall@1",
    wikiRecall3: "Wiki Recall@3",
    wikiLatency: "Wiki 平均时延",
    mem0Status: "Mem0 状态",
    offline: "离线降级",
    normal: "正常",
    fail: "故障",
    warn: "注意",
    rank: "Rank",
    notMatched: "未找到",
    evalHintHigh: "Wiki 召回稳定，可以继续观察 Mem0 通道是否需要打开完整评测。",
    evalHintMed: "Wiki 召回可用但仍有提升空间，建议检查低排名用例的标题与别名。",
    evalHintLow: "Wiki 召回偏低，建议优先复查索引、别名和最近改动。",
  },
  en: {
    tagline: "Your AI second brain",
    badge: "AI Tools",
    title: "AI Connections",
    intro: "Check Claude / Cursor connections, then run read-only doctor and search recall tests.",
    readOnly: "Read-only",
    readOnlyHint: "These actions do not modify files or configuration.",
    recent: "Recent activity",
    connections: "Connections",
    doctor: "Workspace doctor",
    eval: "Search recall test",
    refresh: "Refresh",
    runDoctor: "Run doctor",
    rerunDoctor: "Run doctor again",
    runEval: "Run search test",
    rerunEval: "Run search test again",
    loading: "Loading...",
    emptyActivity: "No recent activity.",
    unavailable: "Unavailable",
    connected: "Connected",
    disconnected: "Disconnected",
    notFound: "Not found",
    path: "Path",
    cursor: "Cursor",
    claude: "Claude Desktop",
    os: "System",
    command: "Safe command",
    doctorAdvice: "Advice",
    status: "Status",
    ready: "Ready",
    blocked: "Blocked",
    total: "Total",
    activities: "items",
    wikiRecall1: "Wiki Recall@1",
    wikiRecall3: "Wiki Recall@3",
    wikiLatency: "Wiki latency",
    mem0Status: "Mem0 status",
    offline: "Offline",
    normal: "OK",
    fail: "Fail",
    warn: "Warn",
    rank: "Rank",
    notMatched: "Not found",
    evalHintHigh: "Wiki recall is stable; keep watching whether Mem0 needs full evaluation.",
    evalHintMed: "Wiki recall is usable but can improve. Check aliases and titles for lower-ranked cases.",
    evalHintLow: "Wiki recall is low. Review indexes, aliases, and recent changes first.",
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

function cx(...items: Array<string | false | null | undefined>) {
  return items.filter(Boolean).join(" ");
}

function toneFromStatus(value: unknown): Tone {
  const status = String(value || "").toLowerCase();
  if (status === "ok" || status === "ready" || status === "success" || status === "connected") return "ok";
  if (status === "fail" || status === "failed" || status === "error") return "fail";
  if (status === "warn" || status === "warning" || status === "blocked" || status === "unreachable") return "warn";
  return "info";
}

function toneClass(tone: Tone) {
  if (tone === "ok") return "border-tm-ok-border bg-tm-ok-bg text-tm-ok";
  if (tone === "fail") return "border-tm-fail-border bg-tm-fail-bg text-tm-fail";
  if (tone === "warn") return "border-tm-warn-border bg-tm-warn-bg text-tm-warn";
  return "border-tm-border bg-tm-info-bg text-tm-secondary";
}

function StatusPill({ tone, label }: { tone: Tone; label: string }) {
  return <span className={cx("rounded-full border px-2.5 py-1 text-xs font-semibold", toneClass(tone))}>{label}</span>;
}

async function fetchJson<T>(url: string, signal?: AbortSignal): Promise<T> {
  const res = await fetch(url, { signal });
  return (await res.json()) as T;
}

function App() {
  const initialData = useMemo(() => readJsonScript("tm-agent-tools-data"), []);
  const [lang, setLang] = useState<Lang>(initialLanguage);
  const [agentStatus, setAgentStatus] = useState<AgentStatus>((initialData.agent_status as AgentStatus) || {});
  const [activities, setActivities] = useState<ActivityItem[]>([]);
  const [activityError, setActivityError] = useState("");
  const [doctor, setDoctor] = useState<DoctorReport | null>(null);
  const [doctorError, setDoctorError] = useState("");
  const [doctorLoading, setDoctorLoading] = useState(false);
  const [evalPayload, setEvalPayload] = useState<EvalPayload | null>(null);
  const [evalLoading, setEvalLoading] = useState(false);
  const [evalRan, setEvalRan] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const t = (key: keyof typeof copy.zh) => copy[lang][key];

  function toggleLang() {
    setLang((current) => {
      const next = current === "zh" ? "en" : "zh";
      window.localStorage.setItem("tm-lang", next);
      return next;
    });
  }

  async function refreshStatus(signal?: AbortSignal, quiet = false) {
    if (!quiet) setRefreshing(true);
    try {
      const [status, activity] = await Promise.allSettled([
        fetchJson<AgentStatus>("/api/agent/status", signal),
        fetchJson<{ ok?: boolean; items?: ActivityItem[]; error?: string }>("/api/agent/recent-activity", signal),
      ]);
      if (status.status === "fulfilled") setAgentStatus(status.value);
      if (activity.status === "fulfilled" && activity.value.ok) {
        setActivities(activity.value.items || []);
        setActivityError("");
      } else if (activity.status === "fulfilled") {
        setActivityError(activity.value.error || t("unavailable"));
      } else if (activity.reason?.name !== "AbortError") {
        setActivityError(text(activity.reason?.message, t("unavailable")));
      }
    } finally {
      if (!quiet) setRefreshing(false);
    }
  }

  async function runDoctor() {
    setDoctorLoading(true);
    setDoctorError("");
    try {
      const data = await fetchJson<{ ok?: boolean; report?: DoctorReport; error?: string }>("/api/agent/doctor?skip_l2=true");
      if (data.ok && data.report) {
        setDoctor(data.report);
      } else {
        setDoctorError(data.error || t("unavailable"));
      }
    } catch (error) {
      setDoctorError(error instanceof Error ? error.message : t("unavailable"));
    } finally {
      setDoctorLoading(false);
    }
  }

  async function runEval() {
    setEvalLoading(true);
    setEvalRan(true);
    try {
      setEvalPayload(await fetchJson<EvalPayload>("/api/agent/eval?skip_mem0=true"));
    } catch (error) {
      setEvalPayload({ ok: false, error: error instanceof Error ? error.message : t("unavailable") });
    } finally {
      setEvalLoading(false);
    }
  }

  useEffect(() => {
    const controller = new AbortController();
    void refreshStatus(controller.signal, true);
    return () => controller.abort();
  }, []);

  return (
    <DashboardShell active="/agent-tools" lang={lang} onToggleLang={toggleLang} tagline={t("tagline")} badge={t("badge")}>
      <main className="relative z-10 mx-auto max-w-6xl px-6 py-8">
        <motion.section initial={{ opacity: 0, y: 14 }} animate={{ opacity: 1, y: 0 }} className="mb-6">
          <div className="flex flex-col justify-between gap-4 md:flex-row md:items-end">
            <div className="max-w-3xl">
              <div className="mb-3 inline-flex items-center gap-2 rounded-full border border-tm-border bg-tm-card-alt px-3 py-1 text-xs font-semibold text-tm-secondary">
                <ShieldCheck size={14} className="text-tm-accent" />
                {t("readOnly")}
              </div>
              <h1 className="text-4xl font-extrabold tracking-normal text-tm-primary">{t("title")}</h1>
              <p className="mt-3 max-w-2xl text-sm leading-6 text-tm-secondary">{t("intro")}</p>
            </div>
            <button
              type="button"
              onClick={() => void refreshStatus(undefined)}
              disabled={refreshing}
              className="inline-flex h-10 items-center justify-center gap-2 rounded-xl border border-tm-border bg-tm-card px-4 text-sm font-semibold text-tm-secondary hover:border-tm-accent disabled:opacity-60"
            >
              {refreshing ? <Loader2 size={16} className="animate-spin" /> : <RefreshCcw size={16} />}
              {t("refresh")}
            </button>
          </div>
        </motion.section>

        <div className="grid gap-5 lg:grid-cols-[1fr_1.05fr]">
          <div>
            <DashboardCard icon={<Activity size={20} />} title={t("recent")} count={activityError || `${t("total")} ${activities.length} ${t("activities")}`}>
              <RecentActivity items={activities} error={activityError} emptyText={t("emptyActivity")} />
            </DashboardCard>
          </div>

          <div>
            <DashboardCard icon={<Bot size={20} />} title={t("connections")} count={text(agentStatus.os, t("unavailable"))}>
              <div className="grid gap-3 md:grid-cols-2">
                <ConnectionCard name={t("cursor")} info={agentStatus.cursor} labels={copy[lang]} />
                <ConnectionCard name={t("claude")} info={agentStatus.claude} labels={copy[lang]} />
              </div>
              <div className="mt-4 rounded-xl border border-tm-border bg-tm-card-alt p-3">
                <div className="flex items-center gap-2 text-xs font-semibold text-tm-tertiary">
                  <TerminalSquare size={15} className="text-tm-accent" />
                  {t("command")}
                </div>
                <code className="mt-2 block rounded-lg border border-tm-border bg-tm-bg px-3 py-2 text-xs text-tm-primary">tm agent-connect</code>
                <p className="mt-2 text-xs text-tm-secondary">{t("readOnlyHint")}</p>
              </div>
            </DashboardCard>
          </div>
        </div>

        <div className="grid gap-5 lg:grid-cols-2">
          <DashboardCard icon={<ClipboardCheck size={20} />} title={t("doctor")}>
            <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
              <StatusPill tone={doctor ? toneFromStatus(doctor.status) : doctorError ? "fail" : "info"} label={doctor ? text(doctor.status, t("status")) : doctorError ? t("fail") : t("unavailable")} />
              <button
                type="button"
                onClick={() => void runDoctor()}
                disabled={doctorLoading}
                className="inline-flex h-9 items-center gap-2 rounded-xl bg-tm-accent px-3 text-sm font-bold text-tm-accent-fg hover:brightness-105 disabled:opacity-60"
              >
                {doctorLoading ? <Loader2 size={16} className="animate-spin" /> : <Play size={16} />}
                {doctor ? t("rerunDoctor") : t("runDoctor")}
              </button>
            </div>
            <DoctorPanel report={doctor} error={doctorError} loading={doctorLoading} labels={copy[lang]} />
          </DashboardCard>

          <DashboardCard icon={<SearchCheck size={20} />} title={t("eval")}>
            <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
              <StatusPill tone={evalPayload?.ok ? "ok" : evalPayload && !evalPayload.ok ? "fail" : "info"} label={evalPayload?.ok ? t("normal") : evalPayload ? t("fail") : t("unavailable")} />
              <button
                type="button"
                onClick={() => void runEval()}
                disabled={evalLoading}
                className="inline-flex h-9 items-center gap-2 rounded-xl bg-tm-accent px-3 text-sm font-bold text-tm-accent-fg hover:brightness-105 disabled:opacity-60"
              >
                {evalLoading ? <Loader2 size={16} className="animate-spin" /> : <RefreshCcw size={16} />}
                {evalRan ? t("rerunEval") : t("runEval")}
              </button>
            </div>
            <EvalPanel payload={evalPayload} loading={evalLoading} labels={copy[lang]} />
          </DashboardCard>
        </div>
      </main>
    </DashboardShell>
  );
}

function RecentActivity({ items, error, emptyText }: { items: ActivityItem[]; error: string; emptyText: string }) {
  if (error) return <div className="rounded-xl border border-tm-fail-border bg-tm-fail-bg p-3 text-sm text-tm-fail">{error}</div>;
  if (!items.length) return <div className="rounded-xl border border-tm-border bg-tm-card-alt p-3 text-sm text-tm-secondary">{emptyText}</div>;
  return (
    <div className="space-y-3">
      <AnimatePresence>
        {items.slice(0, 6).map((item, index) => (
          <motion.article
            key={`${item.type || "activity"}-${item.sha || item.created_at || index}`}
            layout
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -8 }}
            className="rounded-xl border border-tm-border bg-tm-card-alt p-3"
          >
            <div className="flex items-center justify-between gap-3">
              <div className="flex min-w-0 items-center gap-2">
                {item.type === "commit" ? <GitCommit size={16} className="text-tm-accent" /> : <CircleDot size={16} className="text-tm-accent" />}
                <span className="truncate text-sm font-semibold text-tm-primary">{text(item.agent, "agent")}</span>
              </div>
              <span className="rounded-full border border-tm-border bg-tm-bg px-2 py-0.5 text-xs text-tm-tertiary">{text(item.type, "memory")}</span>
            </div>
            <div className="mt-2 truncate text-sm text-tm-secondary">{text(item.title, "-")}</div>
            <div className="mt-1 font-mono text-[11px] text-tm-tertiary">{text(item.created_at || item.sha, "")}</div>
          </motion.article>
        ))}
      </AnimatePresence>
    </div>
  );
}

function MiniMetric({ label, value }: { label: string; value: unknown }) {
  return (
    <div className="rounded-xl border border-tm-border bg-tm-card-alt p-3">
      <div className="text-xs font-semibold text-tm-tertiary">{label}</div>
      <div className="mt-1 text-xl font-extrabold text-tm-primary">{text(value, "0")}</div>
    </div>
  );
}

function ConnectionCard({ name, info, labels }: { name: string; info?: ConnectionInfo; labels: typeof copy.zh }) {
  const exists = Boolean(info?.exists);
  const connected = Boolean(info?.connected);
  const tone: Tone = connected ? "ok" : exists ? "warn" : "info";
  const label = connected ? labels.connected : exists ? labels.disconnected : labels.notFound;
  return (
    <article className="rounded-xl border border-tm-border bg-tm-card-alt p-4">
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2 text-sm font-semibold text-tm-primary">
          <BrainCircuit size={18} className="text-tm-accent" />
          {name}
        </div>
        <StatusPill tone={tone} label={label} />
      </div>
      <div className="mt-3 rounded-lg border border-tm-border bg-tm-bg px-3 py-2 font-mono text-xs text-tm-secondary">
        {exists ? `${labels.path}: ${text(info?.path)}` : labels.notFound}
      </div>
      {info?.error && <div className="mt-2 text-xs text-tm-fail">{info.error}</div>}
    </article>
  );
}

function DoctorPanel({ report, error, loading, labels }: { report: DoctorReport | null; error: string; loading: boolean; labels: typeof copy.zh }) {
  if (loading) return <LoadingLine label={labels.loading} />;
  if (error) return <div className="rounded-xl border border-tm-fail-border bg-tm-fail-bg p-3 text-sm text-tm-fail">{error}</div>;
  if (!report) return <div className="rounded-xl border border-tm-border bg-tm-card-alt p-3 text-sm text-tm-secondary">{labels.readOnlyHint}</div>;
  const checks = report.checks || [];
  return (
    <div className="space-y-3">
      {report.recommended_action && (
        <div className="rounded-xl border border-tm-border bg-tm-card-alt p-3 text-sm text-tm-secondary">
          <div className="mb-1 text-xs font-bold text-tm-tertiary">{labels.doctorAdvice}</div>
          {report.recommended_action}
        </div>
      )}
      <AnimatePresence>
        {checks.map((check) => {
          const tone = toneFromStatus(check.status || (check.ok ? "ok" : "warn"));
          const Icon = tone === "ok" ? CheckCircle2 : tone === "fail" ? XCircle : AlertCircle;
          return (
            <motion.article
              key={text(check.name)}
              layout
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -8 }}
              className="rounded-xl border border-tm-border bg-tm-card-alt p-3"
            >
              <div className="flex items-center justify-between gap-3">
                <div className="flex min-w-0 items-center gap-2">
                  <Icon size={17} className={tone === "ok" ? "text-tm-ok" : tone === "fail" ? "text-tm-fail" : "text-tm-warn"} />
                  <span className="truncate text-sm font-semibold text-tm-primary">{text(check.name)}</span>
                </div>
                <StatusPill tone={tone} label={text(check.status, tone)} />
              </div>
              <div className="mt-2 text-xs text-tm-secondary">
                {check.name === "worktree" && check.ok
                  ? `branch=${text(check.branch)} · ahead=${text(check.ahead, "0")} · behind=${text(check.behind, "0")}`
                  : check.name === "lessons" && check.ok
                    ? `lessons=${text(check.hit_count, "0")}`
                    : text(check.reason || check.error, "")}
              </div>
            </motion.article>
          );
        })}
      </AnimatePresence>
    </div>
  );
}

function EvalPanel({ payload, loading, labels }: { payload: EvalPayload | null; loading: boolean; labels: typeof copy.zh }) {
  if (loading) return <LoadingLine label={labels.loading} />;
  if (!payload) return <div className="rounded-xl border border-tm-border bg-tm-card-alt p-3 text-sm text-tm-secondary">{labels.readOnlyHint}</div>;
  if (!payload.ok) {
    return <div className="rounded-xl border border-tm-fail-border bg-tm-fail-bg p-3 text-sm text-tm-fail">{payload.hint || payload.error || labels.unavailable}</div>;
  }
  const wiki = payload.wiki || {};
  const mem0 = payload.mem0 || {};
  const recall1 = Math.round((wiki.recall_1 || 0) * 100);
  const recall3 = Math.round((wiki.recall_3 || 0) * 100);
  const suggestion = recall1 < 60 ? labels.evalHintLow : recall1 < 80 ? labels.evalHintMed : labels.evalHintHigh;
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
        <MiniMetric label={labels.wikiRecall1} value={`${recall1}%`} />
        <MiniMetric label={labels.wikiRecall3} value={`${recall3}%`} />
        <MiniMetric label={labels.wikiLatency} value={`${Math.round(wiki.avg_latency_ms || 0)} ms`} />
        <MiniMetric label={labels.mem0Status} value={mem0.active ? `${Math.round((mem0.accuracy || 0) * 100)}%` : labels.offline} />
      </div>
      <div className="rounded-xl border border-tm-border bg-tm-card-alt p-3 text-sm text-tm-secondary">{suggestion}</div>
      <div className="max-h-[360px] overflow-auto rounded-xl border border-tm-border">
        <table className="w-full min-w-[560px] text-left text-xs">
          <thead className="bg-tm-card-alt text-tm-tertiary">
            <tr>
              <th className="p-3">ID</th>
              <th className="p-3">Case</th>
              <th className="p-3">Wiki</th>
              <th className="p-3">Latency</th>
              <th className="p-3">Mem0</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-tm-border">
            {(payload.results || []).map((row) => (
              <tr key={text(row.id)} className="bg-tm-card">
                <td className="p-3 font-semibold text-tm-primary">{text(row.id)}</td>
                <td className="p-3 text-tm-secondary">{text(row.description)}</td>
                <td className="p-3">
                  <span className={row.wiki_rank === 1 ? "font-semibold text-tm-ok" : row.wiki_rank && row.wiki_rank > 0 ? "text-tm-warn" : "text-tm-fail"}>
                    {row.wiki_rank && row.wiki_rank > 0 ? `${labels.rank} ${row.wiki_rank}` : labels.notMatched}
                  </span>
                </td>
                <td className="p-3 text-tm-secondary">{text(row.wiki_latency_ms, "0")} ms</td>
                <td className="p-3">
                  <StatusPill tone={row.mem0_status === "SUCCESS" ? "ok" : row.mem0_status === "FAILED" ? "fail" : "warn"} label={text(row.mem0_status, labels.offline)} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function LoadingLine({ label }: { label: string }) {
  return (
    <div className="flex items-center gap-2 rounded-xl border border-tm-border bg-tm-card-alt p-3 text-sm text-tm-secondary">
      <Loader2 size={16} className="animate-spin text-tm-accent" />
      {label}
    </div>
  );
}

createRoot(document.getElementById("root")!).render(<App />);
