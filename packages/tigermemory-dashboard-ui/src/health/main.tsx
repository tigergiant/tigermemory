import {
  Activity,
  BarChart3,
  Brain,
  CalendarDays,
  Database,
  DownloadCloud,
  GitBranch,
  History,
  Loader2,
  RefreshCcw,
  Server,
  ShieldCheck,
  Stethoscope,
} from "lucide-react";
import { AnimatePresence, motion } from "motion/react";
import { useEffect, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";

import { DashboardCard, DashboardShell } from "../components/DashboardShell";
import "../styles.css";

type Lang = "zh" | "en";
type AnyRecord = Record<string, unknown>;

type HealthData = {
  ok?: boolean;
  loading?: boolean;
  generated_at?: string;
  dashboard?: AnyRecord;
  services?: AnyRecord[];
  memory_overview?: AnyRecord;
  agent_doctor?: { checks?: AnyRecord[]; summary?: AnyRecord };
  recent_commits?: string[];
  daily_digest?: AnyRecord;
};

const copy = {
  zh: {
    tagline: "你的 AI 第二大脑",
    badge: "运行检查",
    title: "运行检查",
    intro: "运行检查约每 45 秒自动刷新；基础模式会把高级连接显示为可选。",
    refresh: "刷新",
    autoRefresh: "自动刷新 45s",
    generatedAt: "最后更新",
    services: "关键服务",
    memory: "记忆概览",
    realtime: "实时估算",
    repo: "文件同步",
    digest: "今日整理",
    source: "源码更新",
    selfCheck: "系统自检",
    recent: "最近 5 次保存记录",
    doctor: "健康检查详情",
    clean: "工作区干净，可以继续操作。",
    dirty: "未保存的文件",
    loading: "正在加载运行状态...",
    unavailable: "暂时无法读取",
    platform: "运行平台",
    dual: "两台电脑同步",
    cache: "离线缓存",
    locale: "界面语言",
    ok: "正常",
    warn: "注意",
    fail: "故障",
    optional: "可选",
  },
  en: {
    tagline: "Your AI second brain",
    badge: "Health",
    title: "Health",
    intro: "Health checks refresh every 45 seconds; optional advanced services stay optional in local mode.",
    refresh: "Refresh",
    autoRefresh: "Auto refresh 45s",
    generatedAt: "Updated",
    services: "Services",
    memory: "Memory overview",
    realtime: "Live estimate",
    repo: "Worktree",
    digest: "Daily digest",
    source: "Source update",
    selfCheck: "Self check",
    recent: "Recent commits",
    doctor: "Doctor details",
    clean: "Worktree is clean.",
    dirty: "Dirty files",
    loading: "Loading health status...",
    unavailable: "Unavailable",
    platform: "Platform",
    dual: "Dual machine sync",
    cache: "Offline cache",
    locale: "Language",
    ok: "OK",
    warn: "Warn",
    fail: "Fail",
    optional: "Optional",
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
  if (value === null || value === undefined || value === "") return "—";
  const n = Number(value);
  return Number.isFinite(n) ? n.toLocaleString() : String(value);
}

function statusTone(status: unknown): "ok" | "warn" | "fail" | "optional" {
  const value = String(status || "warn");
  if (value === "ok") return "ok";
  if (value === "fail" || value === "error") return "fail";
  if (value === "optional") return "optional";
  return "warn";
}

function statusClass(status: unknown) {
  const tone = statusTone(status);
  if (tone === "ok") return "border-tm-ok-border bg-tm-ok-bg text-tm-ok";
  if (tone === "fail") return "border-tm-fail-border bg-tm-fail-bg text-tm-fail";
  if (tone === "optional") return "border-tm-border bg-tm-info-bg text-tm-secondary";
  return "border-tm-warn-border bg-tm-warn-bg text-tm-warn";
}

function StatusPill({ status, label }: { status?: unknown; label?: unknown }) {
  return (
    <span className={`rounded-full border px-2.5 py-1 text-xs font-semibold ${statusClass(status)}`}>
      {text(label, text(status, "warn"))}
    </span>
  );
}

function Kpi({ label, value, subline }: { label: string; value: unknown; subline: string }) {
  return (
    <div className="rounded-xl border border-tm-border bg-tm-card-alt p-3">
      <div className="text-xs font-bold text-tm-tertiary">{label}</div>
      <div className="mt-1 text-2xl font-extrabold text-tm-primary">{numberText(value)}</div>
      <div className="mt-1 text-xs text-tm-secondary">{subline}</div>
    </div>
  );
}

function App() {
  const initialData = useMemo(() => readJsonScript("tm-health-data") as HealthData, []);
  const [lang, setLang] = useState<Lang>(initialLanguage);
  const [health, setHealth] = useState<HealthData>(initialData);
  const [memory, setMemory] = useState<AnyRecord>(initialData.memory_overview || {});
  const [refreshing, setRefreshing] = useState(false);
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
    try {
      const [healthRes, memoryRes] = await Promise.all([
        fetch("/api/health/summary").then((r) => r.json()),
        fetch("/api/health/memory-overview").then((r) => r.json()).catch(() => null),
      ]);
      if (healthRes && healthRes.ok !== false) setHealth(healthRes as HealthData);
      if (memoryRes && memoryRes.ok !== false) setMemory(memoryRes as AnyRecord);
    } finally {
      setRefreshing(false);
    }
  }

  useEffect(() => {
    if (health.loading || !health.services?.length) refresh(true);
    const id = window.setInterval(() => {
      if (!document.hidden) refresh(true);
    }, 45000);
    return () => window.clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const services = health.services || [];
  const checks = health.agent_doctor?.checks || [];
  const summary = health.agent_doctor?.summary || {};
  const digest = health.daily_digest || {};
  const dashboard = health.dashboard || {};
  const worktree = checks.find((item) => item.name === "worktree") || {};
  const source = services.find((item) => item.name === "Dashboard") || {};
  const trend = Array.isArray(memory.trend_7d) ? memory.trend_7d : [];

  return (
    <DashboardShell active="/health" lang={lang} onToggleLang={toggleLang} tagline={t("tagline")} badge={t("badge")}>
      <main className="relative z-10 mx-auto max-w-6xl px-5 py-6">
        <DashboardCard>
          <div className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
            <div>
              <div className="flex items-center gap-2 text-sm font-medium text-tm-tertiary">
                <Activity size={16} className="text-tm-accent" />
                <span>TigerMemory</span>
              </div>
              <h1 className="mt-2 text-2xl font-extrabold leading-9 text-tm-primary">{t("title")}</h1>
              <p className="mt-2 text-sm leading-6 text-tm-secondary">{t("intro")}</p>
            </div>
            <div className="flex flex-wrap items-center gap-3">
              <span className="rounded-full border border-tm-border-strong bg-tm-card-alt px-3 py-1 text-xs text-tm-secondary">
                {t("autoRefresh")}
              </span>
              <button
                type="button"
                onClick={() => refresh(false)}
                className="inline-flex items-center gap-2 rounded-md bg-tm-accent px-4 py-2 text-sm font-semibold text-tm-accent-fg hover:bg-tm-accent-hi disabled:opacity-50"
                disabled={refreshing}
              >
                {refreshing ? <Loader2 size={16} className="animate-spin" /> : <RefreshCcw size={16} />}
                {t("refresh")}
              </button>
            </div>
          </div>
          <div className="mt-4 text-sm text-tm-tertiary">
            {t("generatedAt")}: {text(health.generated_at, t("loading"))}
          </div>
        </DashboardCard>

        <DashboardCard icon={<Server size={20} />} title={t("services")} count={`${services.length}`}>
          <div className="grid grid-cols-1 gap-4 md:grid-cols-3 lg:grid-cols-5">
            {services.length ? services.map((service) => <ServiceCard key={text(service.name)} service={service} />) : (
              <div className="rounded-xl border border-tm-border bg-tm-card-alt p-4 text-sm text-tm-tertiary">{t("loading")}</div>
            )}
          </div>
        </DashboardCard>

        <DashboardCard icon={<Brain size={20} />} title={t("memory")} count={memory.ok === false ? t("unavailable") : t("realtime")}>
          <div className="grid gap-3 md:grid-cols-4">
            <Kpi label="Wiki" value={memory.wiki_pages} subline="长期知识" />
            <Kpi label="Inbox" value={memory.inbox_pending} subline="待确认内容" />
            <Kpi label="Mem0" value={memory.mem0_approximate} subline={memory.mem0_approximate == null ? "暂不可达" : "即时记忆"} />
            <Kpi label="7 天日报" value={trend.filter((row) => row && row.available).length} subline="已生成天数" />
          </div>
          <div className="mt-4 grid grid-cols-7 gap-2">
            {trend.slice(-7).map((row, index) => (
              <div key={`${text(row.date)}-${index}`} className="rounded-xl border border-tm-border bg-tm-card-alt p-2 text-center">
                <div className="mx-auto flex h-14 items-end justify-center">
                  <div className={row.available ? "w-5 rounded-t bg-tm-accent" : "w-5 rounded-t bg-tm-border"} style={{ height: row.available ? 36 : 8 }} />
                </div>
                <div className="mt-2 text-[11px] text-tm-tertiary">{text(row.date).slice(5)}</div>
              </div>
            ))}
          </div>
        </DashboardCard>

        <div className="grid gap-5 lg:grid-cols-3">
          <DashboardCard icon={<GitBranch size={20} />} title={t("repo")} count={text(worktree.status, "warn")}>
            <InfoRows rows={[
              ["branch", worktree.branch],
              ["HEAD", text(worktree.head).slice(0, 12)],
              ["upstream", worktree.upstream],
              ["dirty", worktree.dirty_count ?? 0],
            ]} />
            {Number(worktree.dirty_count || 0) > 0 ? (
              <div className="mt-3 rounded-xl border border-tm-fail-border bg-tm-fail-bg p-3 text-sm text-tm-fail">
                {t("dirty")}: {numberText(worktree.dirty_count)}
              </div>
            ) : (
              <div className="mt-3 rounded-xl border border-tm-ok-border bg-tm-ok-bg p-3 text-sm text-tm-ok">{t("clean")}</div>
            )}
          </DashboardCard>

          <DashboardCard icon={<CalendarDays size={20} />} title={t("digest")} count={digest.exists ? t("ok") : t("warn")}>
            <div className="text-2xl font-extrabold text-tm-primary">{text(digest.date)}</div>
            <div className="mt-3 break-all rounded-xl border border-tm-border bg-tm-card-alt p-3 font-mono text-xs text-tm-secondary">
              {text(digest.path)}
            </div>
          </DashboardCard>

          <DashboardCard icon={<DownloadCloud size={20} />} title={t("source")} count={text(source.status, "ok")}>
            <InfoRows rows={[
              ["version", dashboard.version],
              ["git", dashboard.git_sha],
              ["port", dashboard.port],
              ["source", source.source_hash],
            ]} />
          </DashboardCard>
        </div>

        <DashboardCard icon={<ShieldCheck size={20} />} title={t("selfCheck")}>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <Kpi label={t("platform")} value={dashboard.is_wsl ? "WSL2" : "Windows"} subline={text(dashboard.runtime_profile, "local")} />
            <Kpi label={t("dual")} value={text(dashboard.opposite_sha, "—").slice(0, 7)} subline={text(dashboard.git_sha, "—").slice(0, 7)} />
            <Kpi label={t("cache")} value={health.loading ? t("loading") : t("ok")} subline={text(health.cached ? "cached" : "live")} />
            <Kpi label={t("locale")} value={lang === "zh" ? "ZH" : "EN"} subline={lang === "zh" ? "简体中文" : "English"} />
          </div>
        </DashboardCard>

        <DashboardCard icon={<History size={20} />} title={t("recent")}>
          <div className="space-y-2">
            {(health.recent_commits || []).map((line) => (
              <div key={line} className="flex gap-3 rounded-xl border border-tm-border bg-tm-card-alt px-3 py-2 text-sm">
                <span className="mt-2 h-2 w-2 shrink-0 rounded-full bg-tm-accent" />
                <code className="shrink-0 text-xs text-tm-secondary">{line.split(" ")[0]}</code>
                <span className="min-w-0 truncate text-tm-primary">{line.split(" ").slice(1).join(" ")}</span>
              </div>
            ))}
          </div>
        </DashboardCard>

        <DashboardCard icon={<Stethoscope size={20} />} title={t("doctor")} count={`${summary.ok_count || 0}/${summary.warn_count || 0}/${summary.fail_count || 0}`}>
          <div className="grid gap-3 md:grid-cols-2">
            <AnimatePresence>
              {checks.map((check) => (
                <motion.article
                  key={text(check.name)}
                  layout
                  initial={{ opacity: 0, y: 8 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0, y: -8 }}
                  className="rounded-xl border border-tm-border bg-tm-card-alt p-3"
                >
                  <div className="flex items-center justify-between gap-2">
                    <div className="font-semibold text-tm-primary">{text(check.name)}</div>
                    <StatusPill status={check.status} />
                  </div>
                  <div className="mt-2 space-y-1 text-xs text-tm-secondary">
                    {check.latency_ms != null && <div>耗时：{numberText(Math.round(Number(check.latency_ms)))} ms</div>}
                    {check.reason && <div>原因：{text(check.reason)}</div>}
                    {check.error && <div className="text-tm-fail">错误：{text(check.error)}</div>}
                  </div>
                </motion.article>
              ))}
            </AnimatePresence>
          </div>
        </DashboardCard>
      </main>
    </DashboardShell>
  );
}

function ServiceCard({ service }: { service: AnyRecord }) {
  return (
    <article className="rounded-xl border border-tm-border bg-tm-card-alt p-4">
      <div className="mb-3 flex items-center gap-2">
        <Database size={18} className="text-tm-accent" />
        <span className="text-sm font-semibold text-tm-primary">{text(service.name)}</span>
      </div>
      <div className="mb-2 text-2xl font-bold text-tm-primary">
        {service.latency_ms == null ? text(service.status_label, text(service.status)) : `${Math.round(Number(service.latency_ms))} ms`}
      </div>
      <div className="mb-3 truncate text-xs text-tm-tertiary">{text(service.detail || service.source_path)}</div>
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs text-tm-tertiary">{text(service.port, "")}</span>
        <StatusPill status={service.status} label={service.status_label} />
      </div>
    </article>
  );
}

function InfoRows({ rows }: { rows: Array<[string, unknown]> }) {
  return (
    <div className="space-y-2 text-sm text-tm-secondary">
      {rows.map(([label, value]) => (
        <div key={label} className="flex items-start justify-between gap-3 rounded-lg border border-tm-border bg-tm-card-alt px-3 py-2">
          <span className="text-xs font-semibold text-tm-tertiary">{label}</span>
          <code className="min-w-0 break-all text-right text-xs text-tm-primary">{text(value)}</code>
        </div>
      ))}
    </div>
  );
}

createRoot(document.getElementById("root")!).render(<App />);
