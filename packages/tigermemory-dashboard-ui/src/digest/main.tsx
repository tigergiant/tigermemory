import {
  Archive,
  BookMarked,
  BookOpen,
  Brain,
  Check,
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  Inbox,
  Layers,
  Lightbulb,
  Loader2,
  Radar,
  Sparkles,
  TrendingUp,
  X,
} from "lucide-react";
import { AnimatePresence, motion } from "motion/react";
import { useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";

import { ParticleField } from "../ParticleField";
import "../styles.css";

// ---------------------------------------------------------------------------
// Types — mirror the server payloads (see wiki report on /digest data shape).
// ---------------------------------------------------------------------------
type AnyRecord = Record<string, unknown>;

type InboxRow = {
  path: string;
  group?: string;
  stale_archive?: boolean;
  age_days?: number;
  title_cn?: string;
  preview_cn?: string;
  summary?: string;
  raw_summary?: string;
  action?: string;
  reason?: string;
  route_target?: AnyRecord | string;
  route_label?: string;
  route_confidence?: number;
  route_reason?: string;
  route_flags?: string[];
  route_hard_rule?: boolean | string;
};

type Counts = Record<string, number | null | undefined>;

type DigestData = {
  date?: string;
  loading?: boolean;
  counts?: Counts;
  decision?: string;
  summary?: string;
  discard_candidates?: string;
  metrics?: string;
  appendix?: string;
  inbox_rows?: InboxRow[];
  hidden_inbox_rows?: InboxRow[];
  self_evolution?: AnyRecord;
  proposals?: AnyRecord[];
  wiki_proposal_ledger?: AnyRecord[];
};

type CronReport = {
  kind?: string;
  date?: string;
  exists?: boolean;
  status?: string;
  counts?: Counts;
  learning_card?: string[];
  friendly_closeout?: string[];
  actions?: string[];
  issues?: string[];
  summary?: string[];
};

type CronIntake = {
  status?: string;
  date?: string;
  summary?: string;
  reports?: CronReport[];
  warnings?: string[];
  action_items?: string[];
};

// ---------------------------------------------------------------------------
// Bootstrap helpers
// ---------------------------------------------------------------------------
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

function classNames(...items: Array<string | false | null | undefined>) {
  return items.filter(Boolean).join(" ");
}

async function postJson(path: string, body: unknown) {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(String((data as AnyRecord).error || response.statusText));
  return data as AnyRecord & { ok?: boolean; error?: string };
}

// ---------------------------------------------------------------------------
// Shared layout primitives (same design system as /start)
// ---------------------------------------------------------------------------
const NAV = [
  ["/start", "开始"],
  ["/digest", "今日待确认"],
  ["/health", "运行检查"],
  ["/quality", "记忆质量"],
  ["/canvas", "项目进展"],
  ["/self-evolution", "自我进化"],
  ["/agent-tools", "AI 连接"],
  ["/settings", "偏好设置"],
] as const;

const CARD = "rounded-2xl border border-tm-border bg-tm-card shadow-[0_1px_2px_rgba(31,29,27,0.04),0_12px_32px_rgba(168,123,34,0.06)]";
const SECTION_TITLE = "mb-3 flex items-center gap-2 text-lg font-semibold text-tm-primary";

function Header({ active }: { active: string }) {
  return (
    <header className="sticky top-0 z-30 border-b border-tm-border-divider bg-tm-bg/95 backdrop-blur">
      <div className="mx-auto flex max-w-6xl items-center justify-between gap-4 px-6 py-4">
        <a href="/" className="flex w-[220px] shrink-0 select-none items-center gap-3">
          <img src="/static/tiger/tigerlogo.png" alt="" className="h-10 w-10" />
          <span>
            <span className="block text-base font-extrabold leading-none text-tm-primary">TigerMemory</span>
            <span className="mt-0.5 block text-xs text-tm-tertiary">你的 AI 第二大脑</span>
          </span>
        </a>
        <nav className="hidden flex-1 items-center justify-center gap-1 md:flex">
          {NAV.map(([href, label]) => (
            <a
              key={href}
              href={href}
              className={classNames(
                "rounded-xl px-2.5 py-2 text-[13px] leading-5 whitespace-nowrap transition-colors",
                href === active
                  ? "bg-tm-accent font-bold text-tm-inverse shadow-[0_2px_6px_rgba(200,165,96,0.18)]"
                  : "text-tm-secondary hover:bg-tm-card-alt",
              )}
            >
              {label}
            </a>
          ))}
        </nav>
        <div className="flex w-[220px] shrink-0 items-center justify-end gap-2">
          <span className="rounded-full bg-tm-card-alt px-2 py-1 text-xs text-tm-tertiary">digest</span>
        </div>
      </div>
    </header>
  );
}

function SectionShell({
  icon,
  title,
  count,
  children,
  className,
}: {
  icon: React.ReactNode;
  title: string;
  count?: string;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <motion.section
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.3, ease: [0.22, 1, 0.36, 1] }}
      className={classNames("mb-5 p-4", CARD, className)}
    >
      <h2 className={SECTION_TITLE}>
        <span className="text-tm-accent">{icon}</span>
        <span>{title}</span>
        {count && (
          <span className="ml-auto rounded-full border border-tm-border-divider bg-tm-card-alt px-3 py-1 text-xs text-tm-secondary">
            {count}
          </span>
        )}
      </h2>
      {children}
    </motion.section>
  );
}

// ---------------------------------------------------------------------------
// Toast
// ---------------------------------------------------------------------------
function Toast({ msg }: { msg: { text: string; ok: boolean } | null }) {
  return (
    <AnimatePresence>
      {msg && (
        <motion.div
          initial={{ opacity: 0, y: 18 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: 18 }}
          className={classNames(
            "fixed bottom-6 left-1/2 z-50 max-w-[min(92vw,36rem)] -translate-x-1/2 rounded-lg border px-4 py-3 text-sm shadow-lg",
            msg.ok
              ? "border-tm-ok-border bg-tm-ok-bg text-tm-ok"
              : "border-tm-fail-border bg-tm-fail-bg text-tm-fail",
          )}
        >
          {msg.text}
        </motion.div>
      )}
    </AnimatePresence>
  );
}

// ---------------------------------------------------------------------------
// Inbox row card
// ---------------------------------------------------------------------------
function actionLabel(action: string): string {
  switch (action) {
    case "promote_to_mem0":
    case "promote_mem0":
      return "存入即时记忆";
    case "promote_to_wiki":
    case "promote_wiki":
      return "存入知识库";
    case "archive":
      return "归档";
    case "keep":
    case "keep_in_inbox":
      return "保留";
    default:
      return action;
  }
}

function InboxCard({
  row,
  index,
  selected,
  onToggle,
  onAction,
  busy,
  done,
}: {
  row: InboxRow;
  index: number;
  selected: boolean;
  onToggle: () => void;
  onAction: (action: string) => void;
  busy: boolean;
  done: boolean;
}) {
  const [open, setOpen] = useState(false);
  const hardRule = Boolean(row.route_hard_rule);
  const stale = Boolean(row.stale_archive);
  const recommended = row.action || "";

  const isDisabled = (action: string) => hardRule && action !== mapAction(recommended);

  return (
    <motion.article
      layout
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: done ? 0.5 : 1, y: 0 }}
      exit={{ opacity: 0, scale: 0.97 }}
      transition={{ duration: 0.22 }}
      className={classNames(
        "rounded-xl border bg-tm-card p-3 transition-colors",
        stale ? "border-l-4 border-tm-fail-border border-tm-border" : "border-tm-border",
        done && "line-through",
      )}
    >
      <div className="flex items-start gap-3">
        <input
          type="checkbox"
          checked={selected}
          onChange={onToggle}
          className="mt-1 size-4 shrink-0 accent-tm-accent"
          aria-label="选择"
        />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <strong className="text-sm font-semibold text-tm-primary">
              {row.title_cn || row.path}
            </strong>
            {typeof row.age_days === "number" && row.age_days > 0 && (
              <span className="rounded-full bg-tm-card-alt px-2 py-0.5 text-xs text-tm-tertiary">
                停留 {row.age_days} 天
              </span>
            )}
            {row.route_label && (
              <span className="rounded-full bg-tm-info-bg px-2 py-0.5 text-xs text-tm-secondary">
                {row.route_label}
              </span>
            )}
            {hardRule && (
              <span className="rounded-full bg-tm-fail-bg px-2 py-0.5 text-xs text-tm-fail">硬规则</span>
            )}
          </div>
          {row.summary || row.preview_cn ? (
            <p className="mt-1.5 text-sm leading-6 text-tm-secondary">
              {row.summary || row.preview_cn}
            </p>
          ) : null}
          {row.reason && (
            <p className="mt-1 text-xs text-tm-tertiary">{row.reason}</p>
          )}
        </div>
      </div>

      <div className="mt-2.5 flex flex-wrap items-center gap-2 pl-7">
        {(["archive", "promote_mem0", "promote_wiki", "keep"] as const).map((action) => {
          const isRec = action === mapAction(recommended);
          return (
            <button
              key={action}
              type="button"
              disabled={busy || done || isDisabled(action)}
              onClick={() => onAction(action)}
              className={classNames(
                "inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs font-medium transition-colors disabled:opacity-40",
                isRec && action === "promote_wiki"
                  ? "bg-tm-accent text-tm-primary hover:bg-tm-accent-hi"
                  : action === "archive"
                    ? "bg-tm-fail text-tm-inverse hover:opacity-90"
                    : "bg-tm-card-alt text-tm-secondary hover:bg-tm-overlay",
              )}
            >
              {busy && isRec && <Loader2 size={12} className="animate-spin" />}
              {actionLabel(action)}
            </button>
          );
        })}
        {row.raw_summary && (
          <button
            type="button"
            onClick={() => setOpen((v) => !v)}
            className="ml-auto inline-flex items-center gap-1 text-xs text-tm-tertiary hover:text-tm-secondary"
          >
            {open ? <ChevronUp size={13} /> : <ChevronDown size={13} />}
            详情
          </button>
        )}
      </div>

      <AnimatePresence initial={false}>
        {open && row.raw_summary && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            className="overflow-hidden"
          >
            <pre className="mt-2 whitespace-pre-wrap pl-7 text-xs leading-5 text-tm-tertiary">
              {row.raw_summary}
            </pre>
          </motion.div>
        )}
      </AnimatePresence>
      {index >= 0 && <span className="sr-only">{index}</span>}
    </motion.article>
  );
}

function mapAction(serverAction: string): string {
  if (serverAction === "promote_to_mem0") return "promote_mem0";
  if (serverAction === "promote_to_wiki") return "promote_wiki";
  return serverAction;
}

// ---------------------------------------------------------------------------
// Cron intake card
// ---------------------------------------------------------------------------
function CronIntakeSection({ intake }: { intake: CronIntake | null }) {
  if (!intake) return null;
  const statusColor =
    intake.status === "ok"
      ? "bg-tm-ok-bg text-tm-ok border-tm-ok-border"
      : intake.status === "warn" || intake.status === "partial"
        ? "bg-tm-warn-bg text-tm-warn border-tm-warn-border"
        : "bg-tm-fail-bg text-tm-fail border-tm-fail-border";
  const learning =
    (intake.reports?.find((r) => r.kind === "memory_digest")?.learning_card ?? []).concat(
      intake.reports?.find((r) => r.kind === "ai_agent_radar")?.friendly_closeout ?? [],
    );

  return (
    <SectionShell icon={<Radar size={20} />} title="cron 承接卡">
      <div className="mb-3 flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
        <span
          className={classNames(
            "rounded-full border px-3 py-1 text-xs",
            statusColor,
          )}
        >
          {intake.status || "加载中"}
        </span>
      </div>
      <p className="text-sm leading-6 text-tm-secondary">{intake.summary || "正在读取短卡..."}</p>

      {intake.action_items?.length ? (
        <div className="mt-3 grid gap-2 md:grid-cols-2">
          {intake.action_items.map((item, i) => (
            <div
              key={i}
              className="flex items-start gap-2 rounded-lg border border-tm-border bg-tm-card-alt px-3 py-2 text-sm text-tm-secondary"
            >
              <Check size={14} className="mt-0.5 shrink-0 text-tm-ok" />
              <span>{item}</span>
            </div>
          ))}
        </div>
      ) : null}

      {intake.reports?.length ? (
        <div className="mt-3 flex flex-wrap gap-2">
          {intake.reports.map((r, i) => (
            <span
              key={i}
              className={classNames(
                "rounded-full border px-2.5 py-1 text-xs",
                r.exists && r.status === "ok"
                  ? "border-tm-ok-border bg-tm-ok-bg text-tm-ok"
                  : "border-tm-border-divider bg-tm-card-alt text-tm-tertiary",
              )}
            >
              {r.kind}
            </span>
          ))}
        </div>
      ) : null}

      {learning.length ? (
        <div className="mt-3 text-sm leading-6 text-tm-secondary">
          {learning.map((line, i) => (
            <p key={i}>{line}</p>
          ))}
        </div>
      ) : null}

      {intake.warnings?.length ? (
        <div className="mt-3 text-xs leading-5 text-tm-fail">
          {intake.warnings.slice(0, 5).map((w, i) => (
            <p key={i}>{w}</p>
          ))}
        </div>
      ) : null}
    </SectionShell>
  );
}

// ---------------------------------------------------------------------------
// Wiki proposal ledger
// ---------------------------------------------------------------------------
function WikiLedgerSection({ rows }: { rows: AnyRecord[] }) {
  if (!rows.length) return null;
  return (
    <SectionShell
      icon={<BookMarked size={20} />}
      title="Wiki 提案台账"
      count={`${rows.length} 条`}
    >
      <div className="grid gap-3 md:grid-cols-2">
        {rows.map((row, i) => (
          <div key={i} className="rounded-xl border border-tm-border bg-tm-card p-3">
            <div className="flex items-center justify-between gap-2">
              <code className="truncate text-xs text-tm-secondary">
                {String(row.target_partition || "")}/{String(row.target_slug || "")}
              </code>
              {row.review_label ? (
                <span className="shrink-0 rounded-full bg-tm-info-bg px-2 py-0.5 text-xs text-tm-secondary">
                  {String(row.review_label)}
                </span>
              ) : null}
            </div>
            <p className="mt-1.5 text-sm text-tm-secondary">
              {String(row.count ?? 0)} 条 · 话题 {String(row.topics || "—")} · 代理 {String(row.agents || "—")}
            </p>
            <p className="mt-1 text-xs text-tm-tertiary">
              {String(row.first_date || "")} → {String(row.newest_date || "")}
            </p>
          </div>
        ))}
      </div>
    </SectionShell>
  );
}

// ---------------------------------------------------------------------------
// Main App
// ---------------------------------------------------------------------------
function App() {
  const initialDigest = useMemo(() => readJsonScript("tm-digest-data"), []);
  const initialIntake = useMemo(() => readJsonScript("tm-cron-intake-data"), []);
  const digestRef = useRef<DigestData>(initialDigest as DigestData);
  const [digest, setDigest] = useState<DigestData>(initialDigest as DigestData);
  const [intake, setIntake] = useState<CronIntake | null>(
    Object.keys(initialIntake).length ? (initialIntake as CronIntake) : null,
  );
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [busyPaths, setBusyPaths] = useState<Set<string>>(new Set());
  const [donePaths, setDonePaths] = useState<Set<string>>(new Set());
  const [toast, setToast] = useState<{ text: string; ok: boolean } | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  const date = digest.date || "";
  const counts = digest.counts || {};
  const inboxRows = digest.inbox_rows || [];
  const proposals = digest.proposals || [];
  const ledger = digest.wiki_proposal_ledger || [];
  const staleCount = Number(counts.stale_archive || 0);

  function notify(text: string, ok = true) {
    setToast({ text, ok });
    window.setTimeout(() => setToast(null), 3500);
  }

  async function fetchDigest(quiet = false) {
    if (!date) return;
    if (!quiet) setRefreshing(true);
    try {
      const res = await fetch(`/api/digest/${date}`);
      const data = await res.json();
      if (data?.ok !== false && data?.digest) {
        digestRef.current = data.digest as DigestData;
        setDigest(data.digest as DigestData);
      }
    } catch {
      if (!quiet) notify("刷新失败", false);
    } finally {
      setRefreshing(false);
    }
  }

  async function fetchIntake(quiet = true) {
    if (!date) return;
    try {
      const res = await fetch(`/api/cron/intake/${date}`);
      const data = await res.json();
      if (data?.ok !== false && data?.intake) setIntake(data.intake as CronIntake);
    } catch {
      if (!quiet) notify("承接卡读取失败", false);
    }
  }

  // auto-refresh every 60s while visible
  useEffect(() => {
    const id = window.setInterval(() => {
      if (!document.hidden) {
        fetchDigest(true);
        fetchIntake(true);
      }
    }, 60000);
    const onVis = () => {
      if (!document.hidden) fetchDigest(true);
    };
    document.addEventListener("visibilitychange", onVis);
    return () => {
      window.clearInterval(id);
      document.removeEventListener("visibilitychange", onVis);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [date]);

  async function runInboxAction(path: string, action: string) {
    setBusyPaths((prev) => new Set(prev).add(path));
    try {
      const res = await postJson("/api/inbox/action", { path, action, date });
      if (res.ok !== false) {
        setDonePaths((prev) => new Set(prev).add(path));
        setSelected((prev) => {
          const next = new Set(prev);
          next.delete(path);
          return next;
        });
        notify(`${actionLabel(action)} 完成`);
        await fetchDigest(true);
      } else {
        notify(res.error || "操作失败", false);
      }
    } catch (e) {
      notify(String((e as Error).message || "操作失败"), false);
    } finally {
      setBusyPaths((prev) => {
        const next = new Set(prev);
        next.delete(path);
        return next;
      });
    }
  }

  async function runBatchAction(action: string) {
    const paths = Array.from(selected);
    if (!paths.length) return;
    setBusyPaths((prev) => new Set([...prev, ...paths]));
    try {
      const res = await postJson("/api/inbox/batch-action", { paths, action, date });
      if (res.ok !== false) {
        const success = Number(res.success_count || paths.length);
        notify(`批量${actionLabel(action)}：${success} 条完成`);
        setDonePaths((prev) => new Set([...prev, ...paths]));
        setSelected(new Set());
        await fetchDigest(true);
      } else {
        notify(res.error || "批量操作失败", false);
      }
    } catch (e) {
      notify(String((e as Error).message || "批量操作失败"), false);
    } finally {
      setBusyPaths((prev) => {
        const next = new Set(prev);
        paths.forEach((p) => next.delete(p));
        return next;
      });
    }
  }

  async function archiveAllStale() {
    if (!staleCount) return;
    setRefreshing(true);
    try {
      const res = await postJson("/api/batch/archive-stale", { date });
      if (res.ok !== false) {
        notify(`已归档 ${Number(res.archived?.length || 0)} 条过期收件箱`);
        await fetchDigest(true);
      } else {
        notify(res.error || "归档失败", false);
      }
    } catch (e) {
      notify(String((e as Error).message || "归档失败"), false);
    } finally {
      setRefreshing(false);
    }
  }

  const visibleRows = inboxRows.filter((r) => r.path);

  return (
    <div className="relative min-h-screen">
      <ParticleField />
      <img
        src="/static/tiger/tigerlogo.png"
        alt=""
        aria-hidden="true"
        className="pointer-events-none fixed -bottom-[330px] -left-[180px] z-0 w-[min(1040px,90vw)] select-none opacity-10"
      />
      <Header active="/digest" />

      <main className="relative z-10 mx-auto max-w-6xl px-5 py-6">
        {/* Hero archive banner */}
        {staleCount > 0 && (
          <SectionShell
            icon={<Archive size={20} />}
            title="14 天后自动归档"
            className="border-tm-fail-border bg-tm-fail-bg"
          >
            <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
              <div>
                <h3 className="text-2xl font-extrabold text-tm-fail">{staleCount} 条待归档</h3>
                <p className="text-sm text-tm-fail">这些收件箱已超过 14 天且没有应用记录。</p>
              </div>
              <button
                type="button"
                onClick={archiveAllStale}
                disabled={refreshing}
                className="rounded-md bg-tm-fail px-4 py-2 text-sm font-medium text-tm-inverse hover:opacity-90 disabled:opacity-50"
              >
                一键归档全部
              </button>
            </div>
          </SectionShell>
        )}

        {/* Decision */}
        {digest.decision && (
          <SectionShell icon={<Sparkles size={20} />} title="今日要决策">
            <div className="text-sm leading-7 text-tm-secondary [&_strong]:text-tm-primary [&_strong]:font-semibold">
              <Markdownish text={digest.decision} />
            </div>
          </SectionShell>
        )}

        <CronIntakeSection intake={intake} />
        <WikiLedgerSection rows={ledger} />

        {/* Inbox */}
        <SectionShell
          icon={<Inbox size={20} />}
          title="待确认内容"
          count={`${visibleRows.length} 条`}
        >
          {/* batch toolbar */}
          <AnimatePresence>
            {selected.size > 0 && (
              <motion.div
                initial={{ opacity: 0, height: 0 }}
                animate={{ opacity: 1, height: "auto" }}
                exit={{ opacity: 0, height: 0 }}
                className="mb-3 overflow-hidden"
              >
                <div className="sticky top-[53px] z-20 flex flex-wrap items-center justify-between gap-2 rounded-md border border-tm-warn-border bg-tm-warn-bg px-3 py-2 text-sm text-tm-warn shadow-md">
                  <span>已选择 {selected.size} 条</span>
                  <div className="flex flex-wrap gap-2">
                    <button onClick={() => runBatchAction("archive")} className="rounded-md bg-tm-fail px-3 py-1.5 text-xs text-tm-inverse hover:opacity-90">批量归档</button>
                    <button onClick={() => runBatchAction("promote_mem0")} className="rounded-md bg-tm-warn px-3 py-1.5 text-xs text-tm-inverse hover:opacity-90">存入即时记忆</button>
                    <button onClick={() => runBatchAction("promote_wiki")} className="rounded-md bg-tm-accent px-3 py-1.5 text-xs font-semibold text-tm-primary hover:bg-tm-accent-hi">存入知识库</button>
                    <button onClick={() => setSelected(new Set())} className="rounded-md bg-tm-overlay px-3 py-1.5 text-xs text-tm-secondary hover:bg-tm-border-strong">清空选择</button>
                  </div>
                </div>
              </motion.div>
            )}
          </AnimatePresence>

          {visibleRows.length ? (
            <div className="space-y-3">
              <AnimatePresence>
                {visibleRows.map((row, idx) => (
                  <InboxCard
                    key={row.path}
                    row={row}
                    index={idx}
                    selected={selected.has(row.path)}
                    onToggle={() =>
                      setSelected((prev) => {
                        const next = new Set(prev);
                        if (next.has(row.path)) next.delete(row.path);
                        else next.add(row.path);
                        return next;
                      })
                    }
                    onAction={(action) => runInboxAction(row.path, action)}
                    busy={busyPaths.has(row.path)}
                    done={donePaths.has(row.path)}
                  />
                ))}
              </AnimatePresence>
            </div>
          ) : (
            <div className="flex items-center gap-2 py-6 text-sm text-tm-tertiary">
              <CheckCircle2 size={16} className="text-tm-ok" />
              收件箱已清空
            </div>
          )}
        </SectionShell>

        {/* Proposals */}
        {proposals.length > 0 && (
          <SectionShell icon={<Lightbulb size={20} />} title="AI 修改建议" count={`${proposals.length} 条`}>
            <div className="space-y-3">
              {proposals.map((p, i) => (
                <div key={String(p.id || i)} className="rounded-xl border border-tm-border bg-tm-card p-3">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="rounded-full bg-tm-card-alt px-2 py-0.5 text-xs text-tm-secondary">
                      {String(p.type || "其他")}
                    </span>
                    {p.trigger ? (
                      <span className="text-xs text-tm-tertiary">触发：{String(p.trigger)}</span>
                    ) : null}
                  </div>
                  {p.impact ? (
                    <p className="mt-1.5 text-sm text-tm-secondary">{String(p.impact)}</p>
                  ) : null}
                  {Array.isArray(p.diff) && p.diff.length ? (
                    <pre className="mt-2 overflow-x-auto whitespace-pre-wrap text-xs leading-5 text-tm-tertiary">
                      {(p.diff as string[]).slice(0, 6).join("\n")}
                    </pre>
                  ) : null}
                </div>
              ))}
            </div>
          </SectionShell>
        )}

        {/* Metrics */}
        {digest.metrics && (
          <SectionShell icon={<TrendingUp size={20} />} title="系统自检">
            <pre className="whitespace-pre-wrap text-sm leading-6 text-tm-secondary">
              {digest.metrics}
            </pre>
          </SectionShell>
        )}

        {/* Appendix */}
        {digest.appendix && (
          <SectionShell icon={<BookOpen size={20} />} title="原始材料">
            <details>
              <summary className="cursor-pointer text-sm font-medium text-tm-secondary">
                展开原始明细
              </summary>
              <pre className="mt-3 whitespace-pre-wrap text-xs leading-5 text-tm-tertiary">
                {digest.appendix}
              </pre>
            </details>
          </SectionShell>
        )}

        <div className="flex items-center justify-center gap-2 pt-2 pb-4 text-xs text-tm-tertiary">
          {refreshing ? (
            <Loader2 size={13} className="animate-spin" />
          ) : (
            <Layers size={13} />
          )}
          <button onClick={() => fetchDigest(false)} className="hover:text-tm-secondary">
            刷新
          </button>
          <span>· {date || "—"}</span>
        </div>
      </main>

      <Toast msg={toast} />
    </div>
  );
}

// Minimal markdown-ish renderer for the digest decision/summary blocks (bold + line breaks).
function Markdownish({ text }: { text: string }) {
  const html = text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
  return <span dangerouslySetInnerHTML={{ __html: html }} />;
}

createRoot(document.getElementById("root")!).render(<App />);
