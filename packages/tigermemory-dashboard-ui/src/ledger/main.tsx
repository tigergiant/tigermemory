import {
  CheckCircle2,
  Eraser,
  ListChecks,
  Loader2,
  Pencil,
  RefreshCcw,
  RotateCcw,
  Search,
  SkipForward,
  WalletCards,
} from "lucide-react";
import { AnimatePresence, motion } from "motion/react";
import { useEffect, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import type { ReactNode } from "react";

import { DashboardCard, DashboardShell } from "../components/DashboardShell";
import "../styles.css";

type Lang = "zh" | "en";
type AnyRecord = Record<string, unknown>;
type EntryStatus = "pending" | "approved" | "skipped" | "deleted" | "all";

type Summary = {
  ok?: boolean;
  total?: number;
  status_counts?: Record<string, number>;
  live_expense_total?: number;
  live_income_total?: number;
};

type LedgerEntry = {
  id: number;
  review_status?: string;
  occurred_at?: string;
  kind?: string;
  amount?: number;
  category?: string;
  merchant?: string;
  payment_method?: string;
  note?: string;
  tags_list?: string[];
};

type EntryResponse = {
  ok?: boolean;
  total?: number;
  rows?: LedgerEntry[];
};

type Filters = {
  month: string;
  status: EntryStatus;
  kind: string;
  category: string;
  source_agent: string;
  q: string;
};

const copy = {
  zh: {
    tagline: "你的 AI 第二大脑",
    badge: "记账审批",
    title: "记账审批",
    intro: "查看导入后的账本候选，快速确认、修正或跳过需要处理的流水。",
    steward: "个人记忆控制台",
    connected: "本地账本已连接",
    failed: "账本读取失败",
    refresh: "刷新",
    refreshing: "读取中",
    updated: "状态",
    pending: "待确认",
    approved: "已确认",
    skipped: "已跳过",
    deleted: "已删除",
    all: "全部",
    expenseTotal: "支出合计",
    incomeTotal: "收入合计",
    filters: "筛选",
    month: "月份",
    status: "状态",
    kind: "类型",
    category: "分类",
    source: "来源",
    query: "搜索",
    apply: "应用筛选",
    clear: "清空筛选",
    expense: "支出",
    income: "收入",
    any: "全部",
    categoryPlaceholder: "餐饮 / 交通",
    sourcePlaceholder: "expense-import",
    queryPlaceholder: "商户 / 备注 / tag",
    editor: "修正流水",
    close: "关闭",
    merchant: "商户",
    note: "备注",
    saveApprove: "保存并确认",
    rows: "流水清单",
    rowCount: "共 {total} 条，当前显示 {shown} 条",
    empty: "没有符合条件的流水。",
    loading: "正在读取账本...",
    loaded: "已更新",
    id: "ID",
    time: "时间",
    amount: "金额",
    account: "账户",
    tags: "Tags",
    actions: "操作",
    approve: "确认",
    edit: "编辑",
    skip: "跳过",
    restore: "恢复",
    skipPrompt: "跳过原因",
    skipDefault: "重复或不入账",
  },
  en: {
    tagline: "Your AI second brain",
    badge: "Ledger",
    title: "Ledger Review",
    intro: "Review imported ledger candidates, approve clean rows, fix details, or skip noisy transactions.",
    steward: "Memory steward",
    connected: "Local ledger connected",
    failed: "Ledger unavailable",
    refresh: "Refresh",
    refreshing: "Loading",
    updated: "Status",
    pending: "Pending",
    approved: "Approved",
    skipped: "Skipped",
    deleted: "Deleted",
    all: "All",
    expenseTotal: "Expense total",
    incomeTotal: "Income total",
    filters: "Filters",
    month: "Month",
    status: "Status",
    kind: "Kind",
    category: "Category",
    source: "Source",
    query: "Search",
    apply: "Apply",
    clear: "Clear",
    expense: "Expense",
    income: "Income",
    any: "All",
    categoryPlaceholder: "Dining / Transit",
    sourcePlaceholder: "expense-import",
    queryPlaceholder: "Merchant / note / tag",
    editor: "Edit entry",
    close: "Close",
    merchant: "Merchant",
    note: "Note",
    saveApprove: "Save and approve",
    rows: "Entries",
    rowCount: "{total} total, showing {shown}",
    empty: "No entries match the filters.",
    loading: "Loading ledger...",
    loaded: "Updated",
    id: "ID",
    time: "Time",
    amount: "Amount",
    account: "Account",
    tags: "Tags",
    actions: "Actions",
    approve: "Approve",
    edit: "Edit",
    skip: "Skip",
    restore: "Restore",
    skipPrompt: "Skip reason",
    skipDefault: "Duplicate or not bookable",
  },
} as const;

function readJsonScript(id: string): AnyRecord {
  const node = document.getElementById(id);
  const raw = node?.textContent?.trim();
  if (!raw || raw.startsWith("__TM_")) return {};
  try {
    return JSON.parse(raw) as AnyRecord;
  } catch {
    return {};
  }
}

function initialLanguage(): Lang {
  const stored = window.localStorage.getItem("tm-lang");
  if (stored === "zh" || stored === "en") return stored;
  return window.navigator.language.toLowerCase().startsWith("en") ? "en" : "zh";
}

function currentMonth() {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`;
}

function text(value: unknown, fallback = "-") {
  if (value === null || value === undefined || value === "") return fallback;
  return String(value);
}

function numberText(value: unknown) {
  const n = Number(value || 0);
  return Number.isFinite(n) ? n.toLocaleString() : String(value || 0);
}

function money(value: unknown, lang: Lang) {
  const n = Number(value || 0);
  return new Intl.NumberFormat(lang === "zh" ? "zh-CN" : "en-US", {
    style: "currency",
    currency: "CNY",
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(Number.isFinite(n) ? n : 0);
}

function format(template: string, values: Record<string, string | number>) {
  return Object.entries(values).reduce((current, [key, value]) => current.replace(`{${key}}`, String(value)), template);
}

function statusLabel(status: unknown, lang: Lang) {
  const t = copy[lang];
  const value = text(status, "pending");
  if (value === "pending") return t.pending;
  if (value === "approved") return t.approved;
  if (value === "skipped") return t.skipped;
  if (value === "deleted") return t.deleted;
  if (value === "all") return t.all;
  return value;
}

function kindLabel(kind: unknown, lang: Lang) {
  if (kind === "expense") return copy[lang].expense;
  if (kind === "income") return copy[lang].income;
  return text(kind);
}

function statusClass(status: unknown) {
  if (status === "approved") return "border-tm-ok-border bg-tm-ok-bg text-tm-ok";
  if (status === "skipped" || status === "deleted") return "border-tm-fail-border bg-tm-fail-bg text-tm-fail";
  return "border-tm-warn-border bg-tm-warn-bg text-tm-warn";
}

function ActionButton({
  children,
  icon,
  onClick,
  danger,
  disabled,
}: {
  children: string;
  icon: ReactNode;
  onClick: () => void;
  danger?: boolean;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className={`inline-flex h-8 items-center gap-1.5 rounded-lg border px-2.5 text-xs font-semibold disabled:opacity-50 ${
        danger
          ? "border-tm-fail-border bg-tm-fail-bg text-tm-fail hover:bg-[#e9c5bf]"
          : "border-tm-border bg-tm-card text-tm-secondary hover:border-tm-accent hover:text-tm-primary"
      }`}
    >
      {icon}
      {children}
    </button>
  );
}

function Input({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="grid gap-1 text-xs font-bold leading-5 text-tm-tertiary">
      <span>{label}</span>
      {children}
    </label>
  );
}

function App() {
  const initialData = useMemo(() => readJsonScript("tm-ledger-data"), []);
  const initialLang = initialLanguage();
  const [lang, setLang] = useState<Lang>(initialLang);
  const [filters, setFilters] = useState<Filters>({
    month: text(initialData.month, currentMonth()),
    status: "pending",
    kind: "",
    category: "",
    source_agent: "",
    q: "",
  });
  const [appliedFilters, setAppliedFilters] = useState(filters);
  const [summary, setSummary] = useState<Summary>({});
  const [rows, setRows] = useState<LedgerEntry[]>([]);
  const [total, setTotal] = useState(0);
  const [selected, setSelected] = useState<LedgerEntry | null>(null);
  const [editDraft, setEditDraft] = useState({ category: "", merchant: "", note: "" });
  const [toast, setToast] = useState(copy[initialLang].loading);
  const [loading, setLoading] = useState(false);
  const [busyId, setBusyId] = useState<number | null>(null);
  const t = (key: keyof typeof copy.zh) => copy[lang][key];

  function toggleLang() {
    setLang((current) => {
      const next = current === "zh" ? "en" : "zh";
      window.localStorage.setItem("tm-lang", next);
      return next;
    });
  }

  function setFilter<K extends keyof Filters>(key: K, value: Filters[K]) {
    setFilters((current) => ({ ...current, [key]: value }));
  }

  function buildParams(source: Filters, forSummary = false) {
    const params = new URLSearchParams();
    if (source.month) params.set("month", source.month);
    if (!forSummary) {
      if (source.status) params.set("status", source.status);
      if (source.kind) params.set("kind", source.kind);
      if (source.category) params.set("category", source.category);
      if (source.source_agent) params.set("source_agent", source.source_agent);
      if (source.q) params.set("q", source.q);
      params.set("limit", "300");
    }
    return params.toString();
  }

  async function parseJson<T>(res: Response): Promise<T> {
    const data = await res.json();
    if (!res.ok || data?.ok === false) throw new Error(JSON.stringify(data?.detail || data?.error || data));
    return data as T;
  }

  async function load(nextFilters = appliedFilters, quiet = false) {
    if (!quiet) setLoading(true);
    setToast(t("refreshing"));
    try {
      const [summaryData, entryData] = await Promise.all([
        fetch(`/api/ledger/review/summary?${buildParams(nextFilters, true)}`).then((res) => parseJson<Summary>(res)),
        fetch(`/api/ledger/review/entries?${buildParams(nextFilters)}`).then((res) => parseJson<EntryResponse>(res)),
      ]);
      setSummary(summaryData);
      setRows(entryData.rows || []);
      setTotal(Number(entryData.total || 0));
      setToast(t("loaded"));
    } catch (err) {
      setToast(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }

  async function post(path: string, body?: AnyRecord) {
    const res = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: body ? JSON.stringify(body) : JSON.stringify({}),
    });
    return parseJson<AnyRecord>(res);
  }

  async function runAction(entry: LedgerEntry, action: "approve" | "skip" | "restore") {
    setBusyId(entry.id);
    try {
      if (action === "approve") {
        await post(`/api/ledger/review/entries/${entry.id}/approve`);
      } else if (action === "skip") {
        const reason = window.prompt(t("skipPrompt"), t("skipDefault"));
        if (reason === null) return;
        await post(`/api/ledger/review/entries/${entry.id}/skip`, { reason });
      } else {
        await post(`/api/ledger/review/entries/${entry.id}/restore`);
      }
      await load(appliedFilters, true);
    } catch (err) {
      setToast(err instanceof Error ? err.message : String(err));
    } finally {
      setBusyId(null);
    }
  }

  function openEditor(entry: LedgerEntry) {
    setSelected(entry);
    setEditDraft({
      category: entry.category || "",
      merchant: entry.merchant || "",
      note: entry.note || "",
    });
  }

  async function saveEdit() {
    if (!selected) return;
    setBusyId(selected.id);
    try {
      await post(`/api/ledger/review/entries/${selected.id}/edit`, { ...editDraft, approve: true });
      setSelected(null);
      await load(appliedFilters, true);
    } catch (err) {
      setToast(err instanceof Error ? err.message : String(err));
    } finally {
      setBusyId(null);
    }
  }

  function applyFilters() {
    setAppliedFilters(filters);
    void load(filters);
  }

  function clearFilters() {
    const next: Filters = { month: currentMonth(), status: "pending", kind: "", category: "", source_agent: "", q: "" };
    setFilters(next);
    setAppliedFilters(next);
    void load(next);
  }

  useEffect(() => {
    void load(filters);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const counts = summary.status_counts || {};
  const metrics = [
    [t("pending"), counts.pending || 0],
    [t("approved"), counts.approved || 0],
    [t("skipped"), counts.skipped || 0],
    [t("expenseTotal"), money(summary.live_expense_total, lang)],
    [t("incomeTotal"), money(summary.live_income_total, lang)],
  ];

  return (
    <DashboardShell active="/ledger" lang={lang} onToggleLang={toggleLang} tagline={t("tagline")} badge={t("badge")}>
      <main className="relative z-10 mx-auto max-w-6xl px-6 py-8">
        <motion.section initial={{ opacity: 0, y: 14 }} animate={{ opacity: 1, y: 0 }} className="mb-6 rounded-2xl border border-tm-border bg-tm-card p-5 shadow-[0_1px_2px_rgba(31,29,27,0.04),0_12px_32px_rgba(168,123,34,0.06)]">
          <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
            <div>
              <div className="flex items-center gap-2 text-sm font-medium text-tm-tertiary">
                <WalletCards size={16} className="text-tm-accent" />
                <span>{t("steward")}</span>
              </div>
              <h1 className="mt-2 text-4xl font-extrabold tracking-normal text-tm-primary">{t("title")}</h1>
              <p className="mt-2 max-w-3xl text-sm leading-6 text-tm-secondary">{t("intro")}</p>
            </div>
            <div className="flex flex-wrap items-center gap-3">
              <span className="rounded-full border border-tm-border-strong bg-tm-card-alt px-3 py-1 text-xs text-tm-secondary">
                {summary.ok === false ? t("failed") : t("connected")}
              </span>
              <button type="button" onClick={() => void load(appliedFilters)} disabled={loading} className="inline-flex h-9 items-center gap-2 rounded-xl border border-tm-border bg-tm-card-alt px-3 text-sm font-semibold text-tm-secondary hover:border-tm-accent disabled:opacity-60">
                {loading ? <Loader2 size={15} className="animate-spin" /> : <RefreshCcw size={15} />}
                {loading ? t("refreshing") : t("refresh")}
              </button>
            </div>
          </div>
          <div className="mt-4 text-sm text-tm-tertiary">{t("updated")}: {toast}</div>
        </motion.section>

        <section className="mb-6 grid gap-3 md:grid-cols-5">
          {metrics.map(([label, value]) => (
            <motion.article key={String(label)} layout className="rounded-xl border border-tm-border bg-tm-card-alt p-4">
              <span className="block text-xs font-bold text-tm-tertiary">{label}</span>
              <b className="mt-2 block text-2xl font-extrabold text-tm-primary">{String(value)}</b>
            </motion.article>
          ))}
        </section>

        <DashboardCard icon={<Search size={20} />} title={t("filters")}>
          <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-6">
            <Input label={t("month")}><input className="h-10 rounded-xl border border-tm-border-strong bg-tm-card px-3 text-sm text-tm-primary outline-none focus:border-tm-accent" type="month" value={filters.month} onChange={(event) => setFilter("month", event.target.value)} /></Input>
            <Input label={t("status")}><select className="h-10 rounded-xl border border-tm-border-strong bg-tm-card px-3 text-sm text-tm-primary outline-none focus:border-tm-accent" value={filters.status} onChange={(event) => setFilter("status", event.target.value as EntryStatus)}><option value="pending">{t("pending")}</option><option value="approved">{t("approved")}</option><option value="skipped">{t("skipped")}</option><option value="deleted">{t("deleted")}</option><option value="all">{t("all")}</option></select></Input>
            <Input label={t("kind")}><select className="h-10 rounded-xl border border-tm-border-strong bg-tm-card px-3 text-sm text-tm-primary outline-none focus:border-tm-accent" value={filters.kind} onChange={(event) => setFilter("kind", event.target.value)}><option value="">{t("any")}</option><option value="expense">{t("expense")}</option><option value="income">{t("income")}</option></select></Input>
            <Input label={t("category")}><input className="h-10 rounded-xl border border-tm-border-strong bg-tm-card px-3 text-sm text-tm-primary outline-none focus:border-tm-accent" placeholder={t("categoryPlaceholder")} value={filters.category} onChange={(event) => setFilter("category", event.target.value)} /></Input>
            <Input label={t("source")}><input className="h-10 rounded-xl border border-tm-border-strong bg-tm-card px-3 text-sm text-tm-primary outline-none focus:border-tm-accent" placeholder={t("sourcePlaceholder")} value={filters.source_agent} onChange={(event) => setFilter("source_agent", event.target.value)} /></Input>
            <Input label={t("query")}><input className="h-10 rounded-xl border border-tm-border-strong bg-tm-card px-3 text-sm text-tm-primary outline-none focus:border-tm-accent" placeholder={t("queryPlaceholder")} value={filters.q} onChange={(event) => setFilter("q", event.target.value)} /></Input>
          </div>
          <div className="mt-4 flex flex-wrap gap-3">
            <button type="button" onClick={applyFilters} className="inline-flex h-10 items-center gap-2 rounded-xl bg-tm-accent px-4 text-sm font-semibold text-tm-accent-fg hover:bg-tm-accent-hi"><CheckCircle2 size={16} />{t("apply")}</button>
            <button type="button" onClick={clearFilters} className="inline-flex h-10 items-center gap-2 rounded-xl border border-tm-border bg-tm-card-alt px-4 text-sm font-semibold text-tm-secondary hover:border-tm-accent"><Eraser size={16} />{t("clear")}</button>
          </div>
        </DashboardCard>

        <AnimatePresence>
          {selected && (
            <motion.section initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -8 }} className="mb-5 rounded-2xl border border-tm-border bg-tm-card p-4 shadow-[0_1px_2px_rgba(31,29,27,0.04),0_12px_32px_rgba(168,123,34,0.06)]">
              <div className="mb-4 flex items-center justify-between gap-3">
                <h2 className="flex items-center gap-2 text-lg font-semibold text-tm-primary"><Pencil size={20} className="text-tm-accent" />{t("editor")} #{selected.id}</h2>
                <button type="button" onClick={() => setSelected(null)} className="rounded-xl border border-tm-border bg-tm-card-alt px-3 py-2 text-sm font-semibold text-tm-secondary hover:border-tm-accent">{t("close")}</button>
              </div>
              <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
                <Input label={t("category")}><input className="h-10 rounded-xl border border-tm-border-strong bg-tm-card px-3 text-sm text-tm-primary outline-none focus:border-tm-accent" value={editDraft.category} onChange={(event) => setEditDraft((current) => ({ ...current, category: event.target.value }))} /></Input>
                <Input label={t("merchant")}><input className="h-10 rounded-xl border border-tm-border-strong bg-tm-card px-3 text-sm text-tm-primary outline-none focus:border-tm-accent" value={editDraft.merchant} onChange={(event) => setEditDraft((current) => ({ ...current, merchant: event.target.value }))} /></Input>
                <Input label={t("note")}><textarea className="min-h-10 rounded-xl border border-tm-border-strong bg-tm-card px-3 py-2 text-sm text-tm-primary outline-none focus:border-tm-accent lg:col-span-2" rows={2} value={editDraft.note} onChange={(event) => setEditDraft((current) => ({ ...current, note: event.target.value }))} /></Input>
              </div>
              <button type="button" onClick={() => void saveEdit()} disabled={busyId === selected.id} className="mt-4 inline-flex h-10 items-center gap-2 rounded-xl bg-tm-accent px-4 text-sm font-semibold text-tm-accent-fg hover:bg-tm-accent-hi disabled:opacity-60">
                {busyId === selected.id ? <Loader2 size={16} className="animate-spin" /> : <CheckCircle2 size={16} />}{t("saveApprove")}
              </button>
            </motion.section>
          )}
        </AnimatePresence>

        <DashboardCard icon={<ListChecks size={20} />} title={t("rows")} count={format(t("rowCount"), { total: numberText(total), shown: numberText(rows.length) })}>
          <div className="overflow-auto rounded-2xl border border-tm-border bg-tm-card">
            <table className="min-w-[1180px] w-full border-collapse">
              <thead><tr className="bg-tm-card-alt text-left text-xs font-extrabold text-tm-tertiary">{[t("id"), t("status"), t("time"), t("kind"), t("amount"), t("category"), t("merchant"), t("account"), t("note"), t("tags"), t("actions")].map((head) => <th key={head} className="border-b border-tm-border px-3 py-3">{head}</th>)}</tr></thead>
              <tbody>
                {rows.map((row) => (
                  <motion.tr key={row.id} layout className={selected?.id === row.id ? "bg-tm-warn-bg" : "bg-tm-card"}>
                    <td className="border-b border-tm-border px-3 py-3 text-sm text-tm-primary">{row.id}</td>
                    <td className="border-b border-tm-border px-3 py-3"><span className={`inline-flex rounded-full border px-2.5 py-1 text-xs font-bold ${statusClass(row.review_status)}`}>{statusLabel(row.review_status, lang)}</span></td>
                    <td className="border-b border-tm-border px-3 py-3 text-xs text-tm-secondary">{text(row.occurred_at, "")}</td>
                    <td className="border-b border-tm-border px-3 py-3 text-sm text-tm-secondary">{kindLabel(row.kind, lang)}</td>
                    <td className="border-b border-tm-border px-3 py-3 text-sm font-bold text-tm-primary">{money(row.amount, lang)}</td>
                    <td className="border-b border-tm-border px-3 py-3 text-sm text-tm-secondary">{text(row.category, "")}</td>
                    <td className="border-b border-tm-border px-3 py-3 text-sm text-tm-secondary">{text(row.merchant, "")}</td>
                    <td className="border-b border-tm-border px-3 py-3 text-sm text-tm-secondary">{text(row.payment_method, "")}</td>
                    <td className="max-w-[220px] break-words border-b border-tm-border px-3 py-3 text-sm text-tm-secondary">{text(row.note, "")}</td>
                    <td className="border-b border-tm-border px-3 py-3"><div className="flex max-w-[220px] flex-wrap gap-1">{(row.tags_list || []).map((tag) => <span key={tag} className="rounded-full border border-tm-border-strong bg-tm-card-alt px-2 py-0.5 text-[11px] text-tm-secondary">{tag}</span>)}</div></td>
                    <td className="border-b border-tm-border px-3 py-3">
                      <div className="flex flex-wrap gap-2">
                        <ActionButton disabled={busyId === row.id} icon={<CheckCircle2 size={13} />} onClick={() => void runAction(row, "approve")}>{t("approve")}</ActionButton>
                        <ActionButton disabled={busyId === row.id} icon={<Pencil size={13} />} onClick={() => openEditor(row)}>{t("edit")}</ActionButton>
                        {row.review_status === "skipped" || row.review_status === "deleted" ? <ActionButton disabled={busyId === row.id} icon={<RotateCcw size={13} />} onClick={() => void runAction(row, "restore")}>{t("restore")}</ActionButton> : <ActionButton disabled={busyId === row.id} icon={<SkipForward size={13} />} danger onClick={() => void runAction(row, "skip")}>{t("skip")}</ActionButton>}
                      </div>
                    </td>
                  </motion.tr>
                ))}
              </tbody>
            </table>
            {!rows.length && <div className="p-6 text-sm text-tm-tertiary">{loading ? t("loading") : t("empty")}</div>}
          </div>
        </DashboardCard>
      </main>
    </DashboardShell>
  );
}

const root = createRoot(document.getElementById("root")!);
root.render(<App />);
