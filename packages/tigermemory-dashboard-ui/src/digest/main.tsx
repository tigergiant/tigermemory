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

import { DashboardCard, DashboardShell } from "../components/DashboardShell";
import "../styles.css";

// ---------------------------------------------------------------------------
// Types — mirror the server payloads (see wiki report on /digest data shape).
// ---------------------------------------------------------------------------
type AnyRecord = Record<string, unknown>;
type Lang = "zh" | "en";

type WikiTarget = {
  partition: string;
  slug: string;
  label?: string;
  path?: string;
  reason?: string;
  recommended?: boolean;
  alternatives?: WikiTarget[];
  similar?: Array<{ path?: string; reason?: string }>;
};

type ActionQueueItem = {
  id: number;
  label: string;
  detail: string;
  status: "queued" | "running" | "done" | "failed";
  paths: string[];
  progress: number;
  message?: string;
};

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
  wiki_target?: WikiTarget;
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

function sleep(ms: number) {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

const copy = {
  zh: {
    tagline: "你的 AI 第二大脑",
    badge: "今日待确认",
    navStart: "开始",
    navDigest: "今日待确认",
    navHealth: "运行检查",
    navQuality: "记忆质量",
    navCanvas: "项目进展",
    navSelf: "自我进化",
    navAgent: "AI 连接",
    navSettings: "偏好设置",
    actionArchive: "归档",
    actionMem0: "存入即时记忆",
    actionWiki: "存入知识库",
    actionKeep: "保留",
    select: "选择",
    details: "详情",
    staleTitle: "14 天后自动归档",
    staleSuffix: "条待归档",
    staleCopy: "这些收件箱已超过 14 天且没有应用记录。",
    archiveAll: "一键归档全部",
    decisionTitle: "今日要决策",
    cronTitle: "cron 承接卡",
    loading: "加载中",
    loadingCron: "正在读取短卡...",
    wikiLedgerTitle: "Wiki 提案台账",
    wikiLedgerSummary: "当前有 Wiki 提案，优先由本线程归并成长期 Wiki。",
    wikiLedgerEmpty: "当前没有待归并的 Wiki proposal。",
    wikiLedgerBatch: "批量写入 Wiki",
    wikiLedgerInvestmentMeta: "投研可写",
    wikiLedgerArchiveMeta: "归档",
    writeInvestmentWiki: "写入投研 Wiki",
    archiveInvestment: "移入投资提案归档",
    archiveInvestmentHint: "生成可检索摘要，不写正式投研页",
    technicalDetails: "查看技术详情",
    targetPage: "目标页",
    samplePaths: "inbox 路径",
    inboxTitle: "待确认内容",
    selectedPrefix: "已选择",
    batchArchive: "批量归档",
    batchMem0: "存入即时记忆",
    batchWiki: "存入知识库",
    clearSelected: "清空选择",
    emptyInbox: "收件箱已清空",
    proposalsTitle: "AI 修改建议",
    metricsTitle: "系统自检",
    appendixTitle: "原始材料",
    appendixExpand: "展开原始明细",
    refresh: "刷新",
    trigger: "触发",
    other: "其他",
    daysStayed: "停留",
    hardRule: "硬规则",
    wikiModalTitle: "写入知识库推荐",
    wikiModalSubtitle: "确认目标页后再写入 Wiki。",
    wikiModalRecommended: "Codex 推荐操作",
    wikiModalAlternatives: "其他可选落点",
    wikiModalSimilar: "相似页面参考",
    wikiModalNoSimilar: "未找到明显相似页面，可以按默认推荐新建。",
    wikiModalBatchCopy: "按每条提案自己的推荐目标写入",
    wikiModalMode: "处理方式",
    confirmWiki: "确认写入知识库",
    cancel: "取消",
    defaultRecommended: "默认推荐",
    done: "完成",
    refreshFailed: "刷新失败",
    intakeFailed: "承接卡读取失败",
    actionFailed: "操作失败",
    batchFailed: "批量操作失败",
    archiveFailed: "归档失败",
    recovered: "页面已复查确认该条已处理。",
  },
  en: {
    tagline: "Your AI second brain",
    badge: "Review",
    navStart: "Start",
    navDigest: "Review",
    navHealth: "Health",
    navQuality: "Quality",
    navCanvas: "Canvas",
    navSelf: "Self evolution",
    navAgent: "AI tools",
    navSettings: "Settings",
    actionArchive: "Archive",
    actionMem0: "Save to memory",
    actionWiki: "Save to Wiki",
    actionKeep: "Keep",
    select: "Select",
    details: "Details",
    staleTitle: "Auto archive after 14 days",
    staleSuffix: "items pending archive",
    staleCopy: "These inbox items are older than 14 days and have no applied record.",
    archiveAll: "Archive all",
    decisionTitle: "Decision today",
    cronTitle: "Cron intake",
    loading: "Loading",
    loadingCron: "Reading intake card...",
    wikiLedgerTitle: "Wiki proposal ledger",
    wikiLedgerSummary: "Wiki proposals are grouped here for this thread to merge into long-term Wiki pages.",
    wikiLedgerEmpty: "No Wiki proposals waiting to merge.",
    wikiLedgerBatch: "Batch write Wiki",
    wikiLedgerInvestmentMeta: "Investment writable",
    wikiLedgerArchiveMeta: "Archive",
    writeInvestmentWiki: "Write investment Wiki",
    archiveInvestment: "Move to investment archive",
    archiveInvestmentHint: "Create a searchable summary, not a formal investment page",
    technicalDetails: "Technical details",
    targetPage: "Target page",
    samplePaths: "Inbox paths",
    inboxTitle: "Pending review",
    selectedPrefix: "Selected",
    batchArchive: "Batch archive",
    batchMem0: "Save to memory",
    batchWiki: "Save to Wiki",
    clearSelected: "Clear selection",
    emptyInbox: "Inbox is clear",
    proposalsTitle: "AI suggestions",
    metricsTitle: "System checks",
    appendixTitle: "Raw material",
    appendixExpand: "Expand raw details",
    refresh: "Refresh",
    trigger: "Trigger",
    other: "Other",
    daysStayed: "days",
    hardRule: "Hard rule",
    wikiModalTitle: "Wiki target recommendation",
    wikiModalSubtitle: "Confirm the target page before writing to the Wiki.",
    wikiModalRecommended: "Codex recommendation",
    wikiModalAlternatives: "Other target options",
    wikiModalSimilar: "Similar pages",
    wikiModalNoSimilar: "No obvious similar page found. The default target can create a new page.",
    wikiModalBatchCopy: "Write each proposal to its own recommended target",
    wikiModalMode: "Handling mode",
    confirmWiki: "Confirm Wiki write",
    cancel: "Cancel",
    defaultRecommended: "Recommended",
    done: "done",
    refreshFailed: "Refresh failed",
    intakeFailed: "Intake card failed",
    actionFailed: "Action failed",
    batchFailed: "Batch action failed",
    archiveFailed: "Archive failed",
    recovered: "The page was rechecked and the item is already handled.",
  },
} as const;

type CopyKey = keyof typeof copy.zh;
type TFn = (key: CopyKey) => string;

function initialLanguage(): Lang {
  const stored = window.localStorage.getItem("tm-lang");
  if (stored === "zh" || stored === "en") return stored;
  return window.navigator.language.toLowerCase().startsWith("en") ? "en" : "zh";
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

function ActionQueueDock({ items, onClear }: { items: ActionQueueItem[]; onClear: () => void }) {
  const active = items.filter((item) => item.status === "queued" || item.status === "running").length;
  const failed = items.filter((item) => item.status === "failed").length;
  const done = items.filter((item) => item.status === "done").length;
  const summary = active ? `${active} 项正在处理` : failed ? `${failed} 项需要查看` : `${done} 项已完成`;
  const latest = items.slice(-6).reverse();
  if (!items.length) return null;
  return (
    <motion.aside
      initial={{ opacity: 0, y: 18, scale: 0.98 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      exit={{ opacity: 0, y: 18, scale: 0.98 }}
      className={classNames(
        "group fixed bottom-5 right-5 z-40 w-[min(360px,calc(100vw-32px))] overflow-hidden rounded-2xl border bg-tm-card shadow-2xl",
        failed ? "border-tm-fail-border" : active ? "border-tm-accent" : "border-tm-border",
      )}
      aria-live="polite"
    >
      <div className="flex items-center gap-3 px-4 py-3">
        <span className="relative flex size-3">
          {active ? <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-tm-accent opacity-60" /> : null}
          <span className={classNames("relative inline-flex size-3 rounded-full", failed ? "bg-tm-fail" : active ? "bg-tm-accent" : "bg-tm-ok")} />
        </span>
        <div className="min-w-0 flex-1">
          <div className="text-sm font-bold text-tm-primary">处理队列</div>
          <div className="truncate text-xs text-tm-tertiary">{summary}</div>
        </div>
        <div className="text-[11px] font-semibold text-tm-tertiary">悬停展开</div>
      </div>
      <div className="max-h-0 overflow-hidden border-t border-transparent transition-all duration-300 group-hover:max-h-[360px] group-hover:border-tm-border">
        <div className="space-y-2 p-3">
          <div className="flex justify-end">
            <button type="button" onClick={onClear} className="rounded-md bg-tm-card-alt px-2 py-1 text-xs text-tm-secondary hover:bg-tm-overlay">
              清理完成项
            </button>
          </div>
          <AnimatePresence initial={false}>
            {latest.map((item) => (
              <motion.div
                layout
                key={item.id}
                initial={{ opacity: 0, y: 8 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -8 }}
                className={classNames(
                  "rounded-xl border bg-tm-card-alt p-3",
                  item.status === "failed" ? "border-tm-fail-border" : item.status === "done" ? "border-tm-ok-border" : "border-tm-border",
                )}
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="truncate text-sm font-semibold text-tm-primary">{item.label}</div>
                    <div className="truncate text-xs text-tm-tertiary">{item.message || item.detail}</div>
                  </div>
                  <div className="shrink-0 text-xs font-semibold text-tm-secondary">{queueStatusLabel(item.status)}</div>
                </div>
                <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-tm-border">
                  <motion.div
                    layout
                    className={classNames("h-full rounded-full", item.status === "failed" ? "bg-tm-fail" : item.status === "done" ? "bg-tm-ok" : "bg-tm-accent")}
                    animate={{ width: `${item.progress}%` }}
                    transition={{ type: "spring", stiffness: 220, damping: 26 }}
                  />
                </div>
              </motion.div>
            ))}
          </AnimatePresence>
        </div>
      </div>
    </motion.aside>
  );
}

function queueStatusLabel(status: ActionQueueItem["status"]) {
  if (status === "running") return "处理中";
  if (status === "done") return "完成";
  if (status === "failed") return "失败";
  return "排队中";
}

// ---------------------------------------------------------------------------
// Inbox row card
// ---------------------------------------------------------------------------
function actionLabel(action: string, t?: TFn): string {
  switch (action) {
    case "promote_to_mem0":
    case "promote_mem0":
      return t ? t("actionMem0") : "存入即时记忆";
    case "promote_to_wiki":
    case "promote_wiki":
      return t ? t("actionWiki") : "存入知识库";
    case "archive":
      return t ? t("actionArchive") : "归档";
    case "keep":
    case "keep_in_inbox":
      return t ? t("actionKeep") : "保留";
    case "investment_archive":
      return t ? t("archiveInvestment") : "移入投资提案归档";
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
  busyAction,
  done,
  t,
}: {
  row: InboxRow;
  index: number;
  selected: boolean;
  onToggle: () => void;
  onAction: (action: string) => void;
  busy: boolean;
  busyAction: string | null;
  done: boolean;
  t: TFn;
}) {
  const [open, setOpen] = useState(false);
  const hardRule = Boolean(row.route_hard_rule);
  const stale = Boolean(row.stale_archive);
  const recommended = row.action || "";
  const archiveBusy = busyAction === "archive";

  const isDisabled = (action: string) => hardRule && action !== mapAction(recommended);

  return (
    <motion.article
      layout
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: done ? 0 : 1, y: done ? -8 : 0, scale: done ? 0.985 : 1 }}
      exit={{ opacity: 0, height: 0, marginTop: 0, scale: 0.97 }}
      transition={{ duration: 0.22 }}
      className={classNames(
        "relative overflow-hidden rounded-xl border bg-tm-card p-3 transition-colors",
        stale ? "border-l-4 border-tm-fail-border border-tm-border" : "border-tm-border",
        archiveBusy && "border-tm-fail-border bg-tm-fail-bg",
        done && "line-through",
      )}
    >
      <AnimatePresence>
        {busy && (
          <motion.div
            initial={{ x: "-100%" }}
            animate={{ x: "100%" }}
            exit={{ opacity: 0 }}
            transition={{ repeat: Infinity, duration: archiveBusy ? 1.1 : 1.5, ease: "easeInOut" }}
            className="pointer-events-none absolute inset-y-0 w-1/3 bg-tm-accent/20"
          />
        )}
      </AnimatePresence>
      <div className="flex items-start gap-3">
        <input
          type="checkbox"
          checked={selected}
          onChange={onToggle}
          className="mt-1 size-4 shrink-0 accent-tm-accent"
          aria-label={t("select")}
        />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <strong className="text-sm font-semibold text-tm-primary">
              {row.title_cn || row.path}
            </strong>
            {typeof row.age_days === "number" && row.age_days > 0 && (
              <span className="rounded-full bg-tm-card-alt px-2 py-0.5 text-xs text-tm-tertiary">
                {t("daysStayed")} {row.age_days}
              </span>
            )}
            {row.route_label && (
              <span className="rounded-full bg-tm-info-bg px-2 py-0.5 text-xs text-tm-secondary">
                {row.route_label}
              </span>
            )}
            {hardRule && (
              <span className="rounded-full bg-tm-fail-bg px-2 py-0.5 text-xs text-tm-fail">{t("hardRule")}</span>
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
          const isActive = busyAction === action;
          return (
            <button
              key={action}
              type="button"
              disabled={busy || done || isDisabled(action)}
              onClick={() => onAction(action)}
              className={classNames(
                "inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs font-medium transition-colors disabled:opacity-40",
                isRec && action === "promote_wiki"
                  ? "bg-tm-accent text-tm-accent-fg hover:bg-tm-accent-hi"
                  : action === "archive"
                    ? "bg-tm-fail text-tm-inverse hover:opacity-90"
                    : "bg-tm-card-alt text-tm-secondary hover:bg-tm-overlay",
              )}
            >
              {isActive && <Loader2 size={12} className="animate-spin" />}
              {actionLabel(action, t)}
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
            {t("details")}
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
      <AnimatePresence>
        {(busy || done) && (
          <motion.div
            initial={{ opacity: 0, y: 6 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: 6 }}
            className={classNames("mt-2 pl-7 text-xs", done ? "text-tm-ok" : archiveBusy ? "text-tm-fail" : "text-tm-warn")}
          >
            {done ? "协助成功，正在移出列表。" : archiveBusy ? "已加入归档队列，正在写入并刷新列表..." : "已加入处理队列，正在写入..."}
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

const WIKI_PARTITIONS = [
  ["operations", "Operations"],
  ["systems", "Systems"],
  ["production", "Production"],
  ["self-evolution", "Self Evolution"],
  ["brand", "Brand"],
  ["investment", "Investment"],
] as const;

function wikiTargetPath(partition: string, slug: string) {
  return `wiki/${partition}/${slug}.md`;
}

function parseWikiTarget(path: unknown): WikiTarget | null {
  const match = String(path || "").replaceAll("\\", "/").match(/^wiki\/([^/]+)\/(.+?)\.md$/);
  if (!match) return null;
  return { partition: match[1], slug: match[2], path: wikiTargetPath(match[1], match[2]) };
}

function coerceWikiTarget(raw: unknown): WikiTarget | null {
  if (!raw || typeof raw !== "object") return null;
  const value = raw as AnyRecord;
  const partition = String(value.partition || "").trim();
  const slug = String(value.slug || "").trim();
  if (!partition || !slug) return null;
  const alternatives = Array.isArray(value.alternatives)
    ? value.alternatives.map(coerceWikiTarget).filter(Boolean) as WikiTarget[]
    : [];
  const similar = Array.isArray(value.similar)
    ? value.similar.map((item) => ({
        path: String((item as AnyRecord).path || ""),
        reason: String((item as AnyRecord).reason || ""),
      }))
    : [];
  return {
    partition,
    slug,
    label: String(value.label || partition),
    path: String(value.path || wikiTargetPath(partition, slug)),
    reason: String(value.reason || ""),
    recommended: Boolean(value.recommended),
    alternatives,
    similar,
  };
}

function targetForInboxRow(row: InboxRow): WikiTarget {
  const fromPayload = coerceWikiTarget(row.wiki_target);
  if (fromPayload) return fromPayload;
  const slug = row.path.split("/").pop()?.replace(/\.md$/i, "").replace(/^\d{4}-\d{2}-\d{2}-\d{4}-/, "") || "inbox-review-note";
  return {
    partition: "systems",
    slug,
    path: wikiTargetPath("systems", slug),
    reason: "缺少后端推荐，临时落到 systems 分区。",
    alternatives: WIKI_PARTITIONS.map(([partition, label]) => ({
      partition,
      slug,
      label,
      path: wikiTargetPath(partition, slug),
      reason: partition === "systems" ? "默认兜底分区。" : `改写到 ${label} 分区，页面名保持一致。`,
      recommended: partition === "systems",
    })),
    similar: [],
  };
}

function targetForLedgerRow(row: AnyRecord): WikiTarget {
  const parsed = parseWikiTarget(row.target);
  const partition = String(row.target_partition || parsed?.partition || "systems").trim();
  const slug = String(row.target_slug || parsed?.slug || "wiki-proposal").trim();
  return {
    partition,
    slug,
    path: wikiTargetPath(partition, slug),
    reason: "根据 wiki proposal 的 frontmatter 推荐该目标页。",
    alternatives: WIKI_PARTITIONS.map(([part, label]) => ({
      partition: part,
      slug,
      label,
      path: wikiTargetPath(part, slug),
      reason: part === partition ? "系统推荐落点。" : `改写到 ${label} 分区，页面名保持一致。`,
      recommended: part === partition,
    })),
    similar: [],
  };
}

function wikiProposalPaths(rows: AnyRecord[]) {
  const seen = new Set<string>();
  const paths: string[] = [];
  rows.forEach((row) => {
    (Array.isArray(row.paths) ? row.paths : []).forEach((path) => {
      const value = String(path || "").trim();
      if (value && !seen.has(value)) {
        seen.add(value);
        paths.push(value);
      }
    });
  });
  return paths;
}

type WikiModalState = {
  kind: "inbox" | "wiki-ledger" | "wiki-ledger-batch";
  title: string;
  subtitle: string;
  paths: string[];
  rows: AnyRecord[];
  target: WikiTarget | null;
};

function WikiTargetModal({
  state,
  busy,
  t,
  onCancel,
  onConfirm,
  onSelect,
}: {
  state: WikiModalState | null;
  busy: boolean;
  t: TFn;
  onCancel: () => void;
  onConfirm: () => void;
  onSelect: (target: WikiTarget) => void;
}) {
  const target = state?.target || null;
  const alternatives = target ? (target.alternatives?.length ? target.alternatives : [target]) : [];
  return (
    <AnimatePresence>
      {state && (
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          className="fixed inset-0 z-50 flex items-center justify-center bg-tm-overlay/70 px-4 py-6 backdrop-blur-sm"
        >
          <motion.div
            initial={{ opacity: 0, y: 18, scale: 0.98 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 18, scale: 0.98 }}
            className="max-h-[88vh] w-full max-w-3xl overflow-y-auto rounded-2xl border border-tm-border bg-tm-bg p-4 shadow-2xl"
          >
            <div className="flex items-start justify-between gap-3">
              <div>
                <h2 className="text-lg font-semibold text-tm-primary">{t("wikiModalTitle")}</h2>
                <p className="mt-1 text-sm text-tm-secondary">{state.subtitle || t("wikiModalSubtitle")}</p>
              </div>
              <button type="button" onClick={onCancel} className="rounded-full bg-tm-card-alt p-2 text-tm-tertiary hover:text-tm-secondary">
                <X size={16} />
              </button>
            </div>

            <div className="mt-4 space-y-4">
              <section className="rounded-xl border border-tm-accent bg-tm-warn-bg p-3">
                <div className="text-sm font-semibold text-tm-primary">{t("wikiModalRecommended")}</div>
                {target ? (
                  <>
                    <div className="mt-1 break-all font-mono text-xs text-tm-secondary">{target.path || wikiTargetPath(target.partition, target.slug)}</div>
                    {target.reason ? <p className="mt-2 text-sm leading-6 text-tm-secondary">{target.reason}</p> : null}
                  </>
                ) : (
                  <p className="mt-2 text-sm leading-6 text-tm-secondary">{t("wikiModalBatchCopy")}，共 {state.paths.length} 条。</p>
                )}
              </section>

              {target ? (
                <section>
                  <div className="mb-2 text-sm font-semibold text-tm-primary">{t("wikiModalAlternatives")}</div>
                  <div className="grid gap-2 md:grid-cols-2">
                    {alternatives.map((item) => {
                      const selected = item.partition === target.partition && item.slug === target.slug;
                      return (
                        <button
                          key={`${item.partition}:${item.slug}`}
                          type="button"
                          onClick={() => onSelect(item)}
                          className={classNames(
                            "w-full rounded-xl border px-3 py-2 text-left text-sm transition-colors",
                            selected ? "border-tm-accent bg-tm-warn-bg text-tm-primary" : "border-tm-border bg-tm-card text-tm-secondary hover:bg-tm-card-alt",
                          )}
                        >
                          <div className="flex flex-wrap items-center gap-2">
                            <span className="font-semibold">{item.label || item.partition}</span>
                            {item.recommended ? <span className="rounded-full bg-tm-accent px-2 py-0.5 text-xs text-tm-accent-fg">{t("defaultRecommended")}</span> : null}
                          </div>
                          <div className="mt-1 break-all font-mono text-xs">{item.path || wikiTargetPath(item.partition, item.slug)}</div>
                          {item.reason ? <div className="mt-1 text-xs text-tm-tertiary">{item.reason}</div> : null}
                        </button>
                      );
                    })}
                  </div>
                </section>
              ) : (
                <section>
                  <div className="mb-2 text-sm font-semibold text-tm-primary">{t("wikiModalMode")}</div>
                  <div className="rounded-xl border border-tm-border bg-tm-card-alt p-3 text-sm leading-6 text-tm-secondary">
                    {t("wikiModalBatchCopy")}。
                  </div>
                </section>
              )}

              {target ? (
                <section>
                  <div className="mb-2 text-sm font-semibold text-tm-primary">{t("wikiModalSimilar")}</div>
                  {target.similar?.length ? (
                    <div className="space-y-2">
                      {target.similar.map((item, i) => (
                        <div key={i} className="rounded-xl border border-tm-border bg-tm-card-alt p-2 text-sm">
                          <div className="break-all font-mono text-xs text-tm-secondary">{item.path}</div>
                          {item.reason ? <div className="mt-1 text-xs text-tm-tertiary">{item.reason}</div> : null}
                        </div>
                      ))}
                    </div>
                  ) : (
                    <p className="text-sm text-tm-tertiary">{t("wikiModalNoSimilar")}</p>
                  )}
                </section>
              ) : null}
            </div>

            <div className="mt-5 flex justify-end gap-2">
              <button type="button" onClick={onCancel} className="rounded-md bg-tm-card-alt px-4 py-2 text-sm text-tm-secondary hover:bg-tm-overlay">
                {t("cancel")}
              </button>
              <button
                type="button"
                onClick={onConfirm}
                disabled={busy}
                className="inline-flex items-center gap-2 rounded-md bg-tm-accent px-4 py-2 text-sm font-semibold text-tm-accent-fg hover:bg-tm-accent-hi disabled:opacity-50"
              >
                {busy ? <Loader2 size={14} className="animate-spin" /> : null}
                {t("confirmWiki")}
              </button>
            </div>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}

// ---------------------------------------------------------------------------
// Cron intake card
// ---------------------------------------------------------------------------
function CronIntakeSection({ intake, t }: { intake: CronIntake | null; t: TFn }) {
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
    <DashboardCard icon={<Radar size={20} />} title={t("cronTitle")}>
      <div className="mb-3 flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
        <span
          className={classNames(
            "rounded-full border px-3 py-1 text-xs",
            statusColor,
          )}
        >
          {intake.status || t("loading")}
        </span>
      </div>
      <p className="text-sm leading-6 text-tm-secondary">{intake.summary || t("loadingCron")}</p>

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
    </DashboardCard>
  );
}

// ---------------------------------------------------------------------------
// Wiki proposal ledger
// ---------------------------------------------------------------------------
function wikiProposalStatusLabel(status: string) {
  const labels: Record<string, string> = {
    pending: "待本线程归并",
    applied: "已处理",
    "investment-wiki": "可写入投研 Wiki",
    "investment-thread": "投资提案归档",
    truncated: "未完全展示",
  };
  return labels[status] || status || "待处理";
}

function wikiProposalCardTitle(row: AnyRecord) {
  const samples = Array.isArray(row.sample_items) ? row.sample_items : [];
  const first = samples[0] as AnyRecord | undefined;
  const title = String(first?.title || row.target || "Wiki 知识提案").replace(/^reason\s*[：:]\s*/i, "");
  return title.length > 64 ? `${title.slice(0, 64)}...` : title;
}

function wikiProposalCardSummary(row: AnyRecord) {
  const samples = Array.isArray(row.sample_items) ? row.sample_items : [];
  const first = samples[0] as AnyRecord | undefined;
  const preview = String(first?.preview || row.preview_cn || row.cn_summary || row.raw_summary || "").trim();
  if (preview) return preview.length > 180 ? `${preview.slice(0, 180)}...` : preview;
  if (row.status === "investment-wiki") return "这条内容被系统判断为有长期保存价值，可以先写入投研 Wiki；证据不足的部分会作为待确认点保留。";
  if (row.status === "investment-thread") return "这条内容被系统判断为有长期保存价值，但需要先进入投资提案归档，暂不直接写入正式投研结论页。";
  return "这条内容被系统判断为有长期保存价值，可在确认后写入长期 Wiki。";
}

function WikiLedgerSection({
  rows,
  busy,
  t,
  onApproveAll,
  onApproveOne,
  onInvestmentArchive,
}: {
  rows: AnyRecord[];
  busy: boolean;
  t: TFn;
  onApproveAll: () => void;
  onApproveOne: (row: AnyRecord) => void;
  onInvestmentArchive: (row: AnyRecord) => void;
}) {
  if (!rows.length) return null;
  const pendingRows = rows.filter((row) => row.status === "pending" || row.status === "investment-wiki");
  const pendingPaths = wikiProposalPaths(pendingRows);
  const investmentWikiGroups = rows.filter((row) => row.status === "investment-wiki").length;
  const investmentArchiveGroups = rows.filter((row) => row.status === "investment-thread").length;
  return (
    <DashboardCard
      icon={<BookMarked size={20} />}
      title={t("wikiLedgerTitle")}
      count={`${rows.length} 条`}
    >
      <div className="mb-3 flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
        <p className="text-sm leading-6 text-tm-secondary">{t("wikiLedgerSummary")}</p>
        {pendingPaths.length ? (
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              onClick={onApproveAll}
              disabled={busy}
              className="rounded-md bg-tm-accent px-3 py-1.5 text-xs font-semibold text-tm-accent-fg hover:bg-tm-accent-hi disabled:opacity-50"
            >
              {t("wikiLedgerBatch")} {pendingPaths.length} 条
            </button>
            <span className="rounded-md border border-tm-border bg-tm-card-alt px-3 py-1.5 text-xs text-tm-secondary">
              {t("wikiLedgerInvestmentMeta")} {investmentWikiGroups} 组 · {t("wikiLedgerArchiveMeta")} {investmentArchiveGroups} 组
            </span>
          </div>
        ) : (
          <span className="rounded-md border border-tm-border bg-tm-card-alt px-3 py-1.5 text-xs text-tm-tertiary">
            {t("wikiLedgerEmpty")}
          </span>
        )}
      </div>
      <div className="grid gap-3 md:grid-cols-2">
        {rows.map((row, i) => {
          const status = String(row.status || "pending");
          const canWriteWiki = status === "pending" || status === "investment-wiki";
          const isInvestment = status === "investment-thread" || status === "investment-wiki";
          const samples = Array.isArray(row.sample_items) ? row.sample_items : [];
          return (
          <div
            key={i}
            className={classNames(
              "rounded-xl border bg-tm-card p-3",
              isInvestment ? "border-tm-primary shadow-[inset_4px_0_0_#c8a560]" : "border-tm-border",
            )}
          >
            <div className="flex items-center justify-between gap-2">
              <h3 className="min-w-0 truncate text-sm font-semibold text-tm-primary">{wikiProposalCardTitle(row)}</h3>
              {row.review_label ? (
                <span className="shrink-0 rounded-full bg-tm-info-bg px-2 py-0.5 text-xs text-tm-secondary">
                  {String(row.review_label)}
                </span>
              ) : null}
            </div>
            <div className="mt-2 flex flex-wrap gap-2">
              <span className="rounded-full border border-tm-border bg-tm-card-alt px-2 py-0.5 text-xs text-tm-tertiary">
                {String(row.count ?? 0)} 条
              </span>
              <span className="rounded-full border border-tm-border bg-tm-card-alt px-2 py-0.5 text-xs text-tm-secondary">
                {wikiProposalStatusLabel(status)}
              </span>
            </div>
            <p className="mt-1.5 text-sm text-tm-secondary">
              {wikiProposalCardSummary(row)}
            </p>
            <p className="mt-1 text-xs text-tm-tertiary">
              {String(row.first_date || "")} → {String(row.newest_date || "")}
            </p>
            <div className="mt-3 flex flex-wrap gap-2">
              {canWriteWiki ? (
                <button
                  type="button"
                  onClick={() => onApproveOne(row)}
                  disabled={busy}
                  className={classNames(
                    "rounded-md px-3 py-1.5 text-xs font-semibold disabled:opacity-50",
                    status === "investment-wiki" ? "border border-tm-primary bg-tm-primary text-tm-accent hover:opacity-90" : "bg-tm-accent text-tm-accent-fg hover:bg-tm-accent-hi",
                  )}
                >
                  {status === "investment-wiki" ? t("writeInvestmentWiki") : t("actionWiki")}
                </button>
              ) : null}
              {status === "investment-thread" ? (
                <>
                  <button
                    type="button"
                    onClick={() => onInvestmentArchive(row)}
                    disabled={busy}
                    className="rounded-md border border-tm-primary bg-tm-primary px-3 py-1.5 text-xs font-semibold text-tm-accent hover:opacity-90 disabled:opacity-50"
                  >
                    {t("archiveInvestment")}
                  </button>
                  <span className="rounded-md border border-tm-accent bg-tm-card px-3 py-1.5 text-xs text-tm-primary">
                    {t("archiveInvestmentHint")}
                  </span>
                </>
              ) : null}
            </div>
            <details className="mt-2 text-xs text-tm-tertiary">
              <summary className="cursor-pointer">{t("technicalDetails")}</summary>
              <div className="mt-2 rounded-lg border border-tm-border bg-tm-card-alt p-2 leading-5">
                <div>{t("targetPage")}：<span className="font-mono">{String(row.target || `${row.target_partition || ""}/${row.target_slug || ""}`)}</span></div>
                <div>topic：{String(row.topics || "-")} · agent：{String(row.agents || "-")}</div>
                {samples.length ? (
                  <div className="mt-2 space-y-2">
                    {samples.slice(0, 2).map((item, j) => (
                      <div key={j} className="rounded-lg border border-tm-border bg-tm-card p-2">
                        <div className="font-semibold text-tm-secondary">{String((item as AnyRecord).title || "未提供标题")}</div>
                        <div className="mt-1 text-tm-tertiary">{String((item as AnyRecord).preview || "")}</div>
                      </div>
                    ))}
                  </div>
                ) : null}
                <div className="mt-2">{t("samplePaths")}：</div>
                <ul className="mt-1 list-disc space-y-1 pl-5">
                  {wikiProposalPaths([row]).map((path) => <li key={path} className="break-all font-mono">{path}</li>)}
                </ul>
              </div>
            </details>
          </div>
        );
        })}
      </div>
    </DashboardCard>
  );
}

// ---------------------------------------------------------------------------
// Main App
// ---------------------------------------------------------------------------
function App() {
  const initialDigest = useMemo(() => readJsonScript("tm-digest-data"), []);
  const initialIntake = useMemo(() => readJsonScript("tm-cron-intake-data"), []);
  const digestRef = useRef<DigestData>(initialDigest as DigestData);
  const [lang, setLang] = useState<Lang>(initialLanguage);
  const [digest, setDigest] = useState<DigestData>(initialDigest as DigestData);
  const [intake, setIntake] = useState<CronIntake | null>(
    Object.keys(initialIntake).length ? (initialIntake as CronIntake) : null,
  );
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [busyPaths, setBusyPaths] = useState<Set<string>>(new Set());
  const [busyActions, setBusyActions] = useState<Record<string, string>>({});
  const [donePaths, setDonePaths] = useState<Set<string>>(new Set());
  const [actionQueue, setActionQueue] = useState<ActionQueueItem[]>([]);
  const [toast, setToast] = useState<{ text: string; ok: boolean } | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [wikiModal, setWikiModal] = useState<WikiModalState | null>(null);
  const queueSeq = useRef(0);

  const date = digest.date || "";
  const t: TFn = (key) => copy[lang][key] || copy.zh[key];
  const counts = digest.counts || {};
  const inboxRows = digest.inbox_rows || [];
  const proposals = digest.proposals || [];
  const ledger = digest.wiki_proposal_ledger || [];
  const staleCount = Number(counts.stale_archive || 0);

  function notify(text: string, ok = true) {
    setToast({ text, ok });
    window.setTimeout(() => setToast(null), 3500);
  }

  function rowTitle(path: string) {
    const rows = [...(digestRef.current.inbox_rows || []), ...(digestRef.current.hidden_inbox_rows || [])];
    const row = rows.find((item) => item.path === path);
    return row?.title_cn || row?.summary || path;
  }

  function enqueueAction(label: string, detail: string, paths: string[]) {
    const id = ++queueSeq.current;
    setActionQueue((prev) => [
      ...prev,
      {
        id,
        label,
        detail,
        paths,
        status: "queued",
        progress: 18,
      },
    ].slice(-8));
    window.setTimeout(() => updateQueueItem(id, { status: "running", progress: 58, message: "正在处理..." }), 80);
    return id;
  }

  function updateQueueItem(id: number, patch: Partial<ActionQueueItem>) {
    setActionQueue((prev) => prev.map((item) => (item.id === id ? { ...item, ...patch } : item)));
  }

  function setPathsBusy(paths: string[], action: string) {
    setBusyPaths((prev) => new Set([...prev, ...paths]));
    setBusyActions((prev) => {
      const next = { ...prev };
      paths.forEach((path) => { next[path] = action; });
      return next;
    });
  }

  function clearPathsBusy(paths: string[]) {
    setBusyPaths((prev) => {
      const next = new Set(prev);
      paths.forEach((path) => next.delete(path));
      return next;
    });
    setBusyActions((prev) => {
      const next = { ...prev };
      paths.forEach((path) => { delete next[path]; });
      return next;
    });
  }

  function toggleLang() {
    setLang((current) => {
      const next = current === "zh" ? "en" : "zh";
      window.localStorage.setItem("tm-lang", next);
      window.dispatchEvent(new CustomEvent("tm-lang-change", { detail: { lang: next } }));
      return next;
    });
  }

  async function fetchDigest(quiet = false): Promise<DigestData | null> {
    if (!date) return null;
    if (!quiet) setRefreshing(true);
    try {
      const res = await fetch(`/api/digest/${date}`);
      const data = await res.json();
      if (data?.ok !== false && data?.digest) {
        digestRef.current = data.digest as DigestData;
        setDigest(data.digest as DigestData);
        return data.digest as DigestData;
      }
    } catch {
      if (!quiet) notify(t("refreshFailed"), false);
    } finally {
      setRefreshing(false);
    }
    return null;
  }

  async function fetchIntake(quiet = true) {
    if (!date) return;
    try {
      const res = await fetch(`/api/cron/intake/${date}`);
      const data = await res.json();
      if (data?.ok !== false && data?.intake) setIntake(data.intake as CronIntake);
    } catch {
      if (!quiet) notify(t("intakeFailed"), false);
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

  function digestHasInboxPath(current: DigestData, path: string) {
    const rows = [...(current.inbox_rows || []), ...(current.hidden_inbox_rows || [])];
    return rows.some((row) => row.path === path);
  }

  async function markCompletedIfPathGone(path: string, label: string, waitMs = 0) {
    const deadline = Date.now() + Math.max(0, waitMs);
    do {
      const refreshed = await fetchDigest(true);
      const current = refreshed || digestRef.current;
      if (!digestHasInboxPath(current, path)) {
        setDonePaths((prev) => new Set(prev).add(path));
        setSelected((prev) => {
          const next = new Set(prev);
          next.delete(path);
          return next;
        });
        notify(`${label}${t("done")}：${t("recovered")}`);
        return true;
      }
      if (Date.now() >= deadline) break;
      await sleep(2500);
    } while (true);
    return false;
  }

  async function markCompletedIfPathGoneAfterError(error: unknown, path: string, label: string) {
    const message = String((error as Error)?.message || "");
    const waitMs = message.includes("任务超时") ? 90000 : 0;
    return markCompletedIfPathGone(path, label, waitMs);
  }

  async function runInboxAction(path: string, action: string, target?: WikiTarget) {
    const label = actionLabel(action, t);
    const queueId = enqueueAction(label, rowTitle(path), [path]);
    setPathsBusy([path], action);
    try {
      const body = target
        ? { path, action, date, partition: target.partition, slug: target.slug }
        : { path, action, date };
      const res = await postJson("/api/inbox/action", body);
      if (res.ok !== false) {
        setDonePaths((prev) => new Set(prev).add(path));
        setSelected((prev) => {
          const next = new Set(prev);
          next.delete(path);
          return next;
        });
        updateQueueItem(queueId, { status: "done", progress: 100, message: `${label}完成` });
        notify(`${label} ${t("done")}`);
        await sleep(action === "archive" ? 700 : 260);
        await fetchDigest(true);
      } else {
        updateQueueItem(queueId, { status: "failed", progress: 100, message: String(res.error || t("actionFailed")) });
        notify(res.error || t("actionFailed"), false);
      }
    } catch (e) {
      if (await markCompletedIfPathGoneAfterError(e, path, label)) {
        updateQueueItem(queueId, { status: "done", progress: 100, message: `${label}完成` });
      } else {
        updateQueueItem(queueId, { status: "failed", progress: 100, message: String((e as Error).message || t("actionFailed")) });
        notify(String((e as Error).message || t("actionFailed")), false);
      }
    } finally {
      clearPathsBusy([path]);
    }
  }

  async function runBatchAction(action: string, explicitPaths?: string[], target?: WikiTarget | null) {
    const paths = explicitPaths || Array.from(selected);
    if (!paths.length) return;
    const label = `批量${actionLabel(action, t)}`;
    const queueId = enqueueAction(label, `${paths.length} 条待处理`, paths);
    setPathsBusy(paths, action);
    try {
      const body = target
        ? { paths, action, date, partition: target.partition, slug_prefix: target.slug }
        : { paths, action, date };
      const res = await postJson("/api/inbox/batch-action", body);
      if (res.ok !== false) {
        const success = Number(res.success_count || paths.length);
        notify(`${t("selectedPrefix")} ${success} 条：${actionLabel(action, t)} ${t("done")}`);
        setDonePaths((prev) => new Set([...prev, ...paths]));
        setSelected(new Set());
        updateQueueItem(queueId, { status: "done", progress: 100, message: `${success} 条处理完成` });
        await sleep(action === "archive" ? 700 : 260);
        await fetchDigest(true);
      } else {
        updateQueueItem(queueId, { status: "failed", progress: 100, message: String(res.error || t("batchFailed")) });
        notify(res.error || t("batchFailed"), false);
      }
    } catch (e) {
      const reconciled: string[] = [];
      for (const path of paths) {
        if (await markCompletedIfPathGone(path, actionLabel(action, t))) reconciled.push(path);
      }
      if (reconciled.length !== paths.length) {
        updateQueueItem(queueId, { status: "failed", progress: 100, message: String((e as Error).message || t("batchFailed")) });
        notify(String((e as Error).message || t("batchFailed")), false);
      } else {
        updateQueueItem(queueId, { status: "done", progress: 100, message: `${reconciled.length} 条已复查完成` });
      }
    } finally {
      clearPathsBusy(paths);
    }
  }

  async function archiveAllStale() {
    if (!staleCount) return;
    const stalePaths = visibleRows.filter((row) => row.stale_archive).map((row) => row.path);
    const queueId = enqueueAction(t("archiveAll"), `${staleCount} 条过期待归档`, stalePaths);
    if (stalePaths.length) setPathsBusy(stalePaths, "archive");
    setRefreshing(true);
    try {
      const res = await postJson("/api/batch/archive-stale", { date });
      if (res.ok !== false) {
        const archived = Number(res.archived?.length || 0);
        notify(`已归档 ${archived} 条过期收件箱`);
        if (stalePaths.length) setDonePaths((prev) => new Set([...prev, ...stalePaths]));
        updateQueueItem(queueId, { status: "done", progress: 100, message: `已归档 ${archived} 条` });
        await sleep(700);
        await fetchDigest(true);
      } else {
        updateQueueItem(queueId, { status: "failed", progress: 100, message: String(res.error || t("archiveFailed")) });
        notify(res.error || t("archiveFailed"), false);
      }
    } catch (e) {
      updateQueueItem(queueId, { status: "failed", progress: 100, message: String((e as Error).message || t("archiveFailed")) });
      notify(String((e as Error).message || t("archiveFailed")), false);
    } finally {
      if (stalePaths.length) clearPathsBusy(stalePaths);
      setRefreshing(false);
    }
  }

  function openWikiModalForInbox(row: InboxRow) {
    setWikiModal({
      kind: "inbox",
      title: row.title_cn || row.path,
      subtitle: row.title_cn || row.path,
      paths: [row.path],
      rows: [row as AnyRecord],
      target: targetForInboxRow(row),
    });
  }

  function openWikiProposal(row: AnyRecord) {
    const paths = wikiProposalPaths([row]);
    if (!paths.length) {
      notify("没有可写入的 Wiki 提案", false);
      return;
    }
    setWikiModal({
      kind: "wiki-ledger",
      title: wikiProposalCardTitle(row),
      subtitle: `${wikiProposalCardTitle(row)} · ${paths.length} 条`,
      paths,
      rows: [row],
      target: targetForLedgerRow(row),
    });
  }

  function openWikiProposalBatch() {
    const rows = ledger.filter((row) => row.status === "pending" || row.status === "investment-wiki");
    const paths = wikiProposalPaths(rows);
    if (!paths.length) {
      notify("没有可写入的 Wiki 提案", false);
      return;
    }
    setWikiModal({
      kind: "wiki-ledger-batch",
      title: `${t("wikiLedgerBatch")} ${paths.length} 条`,
      subtitle: `${t("wikiModalBatchCopy")}，共 ${paths.length} 条`,
      paths,
      rows,
      target: null,
    });
  }

  async function runWikiLedgerAction(row: AnyRecord, action: "promote_wiki" | "investment_archive", target?: WikiTarget | null) {
    const paths = wikiProposalPaths([row]);
    if (!paths.length) {
      notify(action === "investment_archive" ? "没有可归档的投资资料条目" : "没有可写入的 Wiki 提案", false);
      return;
    }
    await runBatchAction(action, paths, target || null);
  }

  async function confirmWikiModal() {
    if (!wikiModal) return;
    const state = wikiModal;
    setWikiModal(null);
    if (state.kind === "inbox" && state.paths[0] && state.target) {
      await runInboxAction(state.paths[0], "promote_wiki", state.target);
      return;
    }
    if (state.kind === "wiki-ledger") {
      await runBatchAction("promote_wiki", state.paths, state.target);
      return;
    }
    await runBatchAction("promote_wiki", state.paths, null);
  }

  const visibleRows = inboxRows.filter((r) => r.path);

  return (
    <DashboardShell active="/digest" lang={lang} onToggleLang={toggleLang} tagline={t("tagline")} badge={t("badge")}>
      <main className="relative z-10 mx-auto max-w-6xl px-5 py-6">
        {/* Hero archive banner */}
        {staleCount > 0 && (
          <DashboardCard
            icon={<Archive size={20} />}
            title={t("staleTitle")}
            className="border-tm-fail-border bg-tm-fail-bg"
          >
            <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
              <div>
                <h3 className="text-2xl font-extrabold text-tm-fail">{staleCount} {t("staleSuffix")}</h3>
                <p className="text-sm text-tm-fail">{t("staleCopy")}</p>
              </div>
              <button
                type="button"
                onClick={archiveAllStale}
                disabled={refreshing}
                className="rounded-md bg-tm-fail px-4 py-2 text-sm font-medium text-tm-inverse hover:opacity-90 disabled:opacity-50"
              >
                {t("archiveAll")}
              </button>
            </div>
          </DashboardCard>
        )}

        {/* Decision */}
        {digest.decision && (
          <DashboardCard icon={<Sparkles size={20} />} title={t("decisionTitle")}>
            <div className="text-sm leading-7 text-tm-secondary [&_strong]:text-tm-primary [&_strong]:font-semibold">
              <Markdownish text={digest.decision} />
            </div>
          </DashboardCard>
        )}

        <CronIntakeSection intake={intake} t={t} />
        <WikiLedgerSection
          rows={ledger}
          busy={Boolean(wikiModal) || busyPaths.size > 0}
          t={t}
          onApproveAll={openWikiProposalBatch}
          onApproveOne={openWikiProposal}
          onInvestmentArchive={(row) => runWikiLedgerAction(row, "investment_archive")}
        />

        {/* Inbox */}
        <DashboardCard
          icon={<Inbox size={20} />}
          title={t("inboxTitle")}
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
                  <span>{t("selectedPrefix")} {selected.size} 条</span>
                  <div className="flex flex-wrap gap-2">
                    <button onClick={() => runBatchAction("archive")} className="rounded-md bg-tm-fail px-3 py-1.5 text-xs text-tm-inverse hover:opacity-90">{t("batchArchive")}</button>
                    <button onClick={() => runBatchAction("promote_mem0")} className="rounded-md bg-tm-warn px-3 py-1.5 text-xs text-tm-inverse hover:opacity-90">{t("batchMem0")}</button>
                    <button
                      onClick={() => {
                        const rows = visibleRows.filter((row) => selected.has(row.path));
                        const paths = rows.map((row) => row.path);
                        setWikiModal({
                          kind: "wiki-ledger-batch",
                          title: `${t("batchWiki")} ${paths.length} 条`,
                          subtitle: `${t("wikiModalBatchCopy")}，共 ${paths.length} 条`,
                          paths,
                          rows: rows as AnyRecord[],
                          target: null,
                        });
                      }}
                      className="rounded-md bg-tm-accent px-3 py-1.5 text-xs font-semibold text-tm-accent-fg hover:bg-tm-accent-hi"
                    >
                      {t("batchWiki")}
                    </button>
                    <button onClick={() => setSelected(new Set())} className="rounded-md bg-tm-overlay px-3 py-1.5 text-xs text-tm-secondary hover:bg-tm-border-strong">{t("clearSelected")}</button>
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
                    onAction={(action) => action === "promote_wiki" ? openWikiModalForInbox(row) : runInboxAction(row.path, action)}
                    busy={busyPaths.has(row.path)}
                    busyAction={busyActions[row.path] || null}
                    done={donePaths.has(row.path)}
                    t={t}
                  />
                ))}
              </AnimatePresence>
            </div>
          ) : (
            <div className="flex items-center gap-2 py-6 text-sm text-tm-tertiary">
              <CheckCircle2 size={16} className="text-tm-ok" />
              {t("emptyInbox")}
            </div>
          )}
        </DashboardCard>

        {/* Proposals */}
        {proposals.length > 0 && (
          <DashboardCard icon={<Lightbulb size={20} />} title={t("proposalsTitle")} count={`${proposals.length} 条`}>
            <div className="space-y-3">
              {proposals.map((p, i) => (
                <div key={String(p.id || i)} className="rounded-xl border border-tm-border bg-tm-card p-3">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="rounded-full bg-tm-card-alt px-2 py-0.5 text-xs text-tm-secondary">
                       {String(p.type || t("other"))}
                    </span>
                    {p.trigger ? (
                      <span className="text-xs text-tm-tertiary">{t("trigger")}：{String(p.trigger)}</span>
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
          </DashboardCard>
        )}

        {/* Metrics */}
        {digest.metrics && (
          <DashboardCard icon={<TrendingUp size={20} />} title={t("metricsTitle")}>
            <pre className="whitespace-pre-wrap text-sm leading-6 text-tm-secondary">
              {digest.metrics}
            </pre>
          </DashboardCard>
        )}

        {/* Appendix */}
        {digest.appendix && (
          <DashboardCard icon={<BookOpen size={20} />} title={t("appendixTitle")}>
            <details>
              <summary className="cursor-pointer text-sm font-medium text-tm-secondary">
                {t("appendixExpand")}
              </summary>
              <pre className="mt-3 whitespace-pre-wrap text-xs leading-5 text-tm-tertiary">
                {digest.appendix}
              </pre>
            </details>
          </DashboardCard>
        )}

        <div className="flex items-center justify-center gap-2 pt-2 pb-4 text-xs text-tm-tertiary">
          {refreshing ? (
            <Loader2 size={13} className="animate-spin" />
          ) : (
            <Layers size={13} />
          )}
          <button onClick={() => fetchDigest(false)} className="hover:text-tm-secondary">
            {t("refresh")}
          </button>
          <span>· {date || "—"}</span>
        </div>
      </main>

      <Toast msg={toast} />
      <ActionQueueDock
        items={actionQueue}
        onClear={() => setActionQueue((prev) => prev.filter((item) => item.status === "queued" || item.status === "running"))}
      />
      <WikiTargetModal
        state={wikiModal}
        busy={busyPaths.size > 0}
        t={t}
        onCancel={() => setWikiModal(null)}
        onConfirm={confirmWikiModal}
        onSelect={(target) => setWikiModal((state) => state ? { ...state, target } : state)}
      />
    </DashboardShell>
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
