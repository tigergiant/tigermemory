import {
  BadgeCheck,
  Bot,
  Check,
  Loader2,
  MessageCircle,
  RotateCcw,
  Save,
  Settings2,
  TimerReset,
  X,
} from "lucide-react";
import { AnimatePresence, motion } from "motion/react";
import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";

import { DashboardCard, DashboardShell } from "../components/DashboardShell";
import "../styles.css";

type Lang = "zh" | "en";
type AnyRecord = Record<string, unknown>;

type Preferences = {
  communication_depth: string;
  exemptions: string[];
  custom_terms: string[];
  progressive_term_frequency: boolean;
  agents: string[];
  model_workflow: string;
  command_timeout_budget: string;
};

type SettingsData = {
  ok?: boolean;
  loading?: boolean;
  path?: string;
  preferences?: Partial<Preferences>;
  defaults?: Partial<Preferences>;
  error?: string;
};

const fallbackPreferences: Preferences = {
  communication_depth: "A",
  exemptions: ["git", "ai", "tigermemory", "agent", "data-format"],
  custom_terms: [],
  progressive_term_frequency: false,
  agents: ["cascade", "claude-code", "codex", "chatgpt", "kimi", "hermes", "openclaw"],
  model_workflow: "开发任务交给 codex；Claude 4.7 仅做监督 / 仲裁 / 设计。",
  command_timeout_budget: "10 / 30 / 60 / 120 秒",
};

const depthOptions = [
  ["A", "A 极简", "只给中文", "OAuth（账号授权）"],
  ["B", "B 简短", "+ 类比", "OAuth（账号授权，类似身份证）"],
  ["C", "C 工程", "+ 上下文", "OAuth（账号授权，tigermemory 用于连接器）"],
  ["D", "D 全套", "三段都给", "账号授权 + 类比 + 系统上下文"],
] as const;

const exemptionOptions = [
  ["git", "git 操作"],
  ["ai", "AI 通用词"],
  ["tigermemory", "tigermemory 内核词"],
  ["agent", "agent 名字"],
  ["data-format", "数据格式"],
] as const;

const agentOptions = ["cascade", "claude-code", "codex", "chatgpt", "kimi", "hermes", "openclaw", "deerflow"];

const timeoutRows = [
  ["local", "本地查询", 5, 60],
  ["wsl", "WSL 写入", 10, 180],
  ["net", "网络调用", 10, 300],
  ["llm", "LLM 调用", 10, 600],
] as const;

const copy = {
  zh: {
    tagline: "你的 AI 第二大脑",
    badge: "偏好设置",
    title: "偏好设置",
    intro: "这些设置先保存在本机。勾选长期同步后，会生成提议再写入个人偏好页，控制台不会直接改敏感资料。",
    steward: "记忆管家",
    depth: "AI 回复详细程度",
    exemptions: "不用解释的词",
    customTerms: "自定义词汇",
    customPlaceholder: "输入词汇并回车...",
    progressive: "少重复解释",
    progressiveDesc: "开启后，同一个词被解释过多次时，会自动少重复，让页面更清爽。",
    progressiveNote: "当前版本只保存开关状态，详细计数功能将在后续版本上线。",
    agents: "这些 AI 助手都适用",
    workflow: "不同 AI 怎么分工",
    timeout: "命令最多等多久",
    proposeWiki: "保存后同步到云端知识库",
    reset: "重置默认",
    save: "保存设置",
    detail: "展开当前设置详情（技术人员用）",
    loading: "正在加载偏好设置...",
    saving: "正在保存设置并同步到云端...",
    savedLocal: "已保存到本地数据库",
    synced: "已同步到云端",
    syncSubmitted: "云端同步已提交或未启用",
    resetPending: "已恢复默认值，尚未保存。",
    duplicate: "自定义词汇已存在",
    error: "出错了",
  },
  en: {
    tagline: "Your AI second brain",
    badge: "Settings",
    title: "Settings",
    intro: "These preferences are saved locally first. Cloud sync creates a proposal instead of directly editing sensitive personal pages.",
    steward: "Memory steward",
    depth: "Answer detail",
    exemptions: "Terms not to explain",
    customTerms: "Custom terms",
    customPlaceholder: "Type a term and press Enter...",
    progressive: "Reduce repeated explanations",
    progressiveDesc: "When enabled, repeated terms are explained less often so pages stay cleaner.",
    progressiveNote: "This version saves the switch only; detailed counters will land later.",
    agents: "Applies to these AI assistants",
    workflow: "AI work split",
    timeout: "Command timeout budget",
    proposeWiki: "Sync to cloud knowledge base after saving",
    reset: "Reset defaults",
    save: "Save settings",
    detail: "Show current settings detail",
    loading: "Loading preferences...",
    saving: "Saving settings and syncing...",
    savedLocal: "Saved to local database",
    synced: "synced to cloud",
    syncSubmitted: "cloud sync submitted or disabled",
    resetPending: "Defaults restored, not saved yet.",
    duplicate: "Custom term already exists",
    error: "Error",
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

function cx(...items: Array<string | false | null | undefined>) {
  return items.filter(Boolean).join(" ");
}

function normalizePrefs(value?: Partial<Preferences>): Preferences {
  const source = value || {};
  return {
    communication_depth: typeof source.communication_depth === "string" ? source.communication_depth : fallbackPreferences.communication_depth,
    exemptions: Array.isArray(source.exemptions) ? source.exemptions.map(String) : [...fallbackPreferences.exemptions],
    custom_terms: Array.isArray(source.custom_terms) ? source.custom_terms.map(String) : [...fallbackPreferences.custom_terms],
    progressive_term_frequency: Boolean(source.progressive_term_frequency),
    agents: Array.isArray(source.agents) ? source.agents.map(String) : [...fallbackPreferences.agents],
    model_workflow: typeof source.model_workflow === "string" ? source.model_workflow : fallbackPreferences.model_workflow,
    command_timeout_budget: typeof source.command_timeout_budget === "string" ? source.command_timeout_budget : fallbackPreferences.command_timeout_budget,
  };
}

function timeoutValues(value: string) {
  const parts = value.replace(/[^\d/]/g, "").split("/").map((item) => Number.parseInt(item, 10));
  return {
    local: Number.isFinite(parts[0]) ? parts[0] : 10,
    wsl: Number.isFinite(parts[1]) ? parts[1] : 30,
    net: Number.isFinite(parts[2]) ? parts[2] : 60,
    llm: Number.isFinite(parts[3]) ? parts[3] : 120,
  };
}

function toTimeoutString(values: Record<string, number>) {
  return `${values.local} / ${values.wsl} / ${values.net} / ${values.llm} 秒`;
}

function StatusToast({ toast }: { toast: { message: string; ok: boolean } | null }) {
  return (
    <AnimatePresence>
      {toast && (
        <motion.div
          initial={{ opacity: 0, y: 18 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: 18 }}
          className={cx(
            "fixed bottom-6 left-1/2 z-50 max-w-[min(92vw,36rem)] -translate-x-1/2 rounded-xl border px-4 py-3 text-sm shadow-lg",
            toast.ok ? "border-tm-ok-border bg-tm-ok-bg text-tm-ok" : "border-tm-fail-border bg-tm-fail-bg text-tm-fail",
          )}
        >
          {toast.message}
        </motion.div>
      )}
    </AnimatePresence>
  );
}

function Chip({ active, children, onClick }: { active: boolean; children: React.ReactNode; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cx(
        "rounded-full border px-3 py-1.5 text-sm font-semibold transition-colors",
        active
          ? "border-tm-accent bg-tm-accent text-tm-primary"
          : "border-tm-border bg-tm-card-alt text-tm-secondary hover:border-tm-accent",
      )}
    >
      {children}
    </button>
  );
}

function App() {
  const initialData = useMemo(() => readJsonScript("tm-settings-data") as SettingsData, []);
  const [lang, setLang] = useState<Lang>(initialLanguage);
  const [path, setPath] = useState(initialData.path || "");
  const [prefs, setPrefs] = useState<Preferences>(() => normalizePrefs(initialData.preferences));
  const [defaults, setDefaults] = useState<Preferences>(() => normalizePrefs(initialData.defaults));
  const [proposeWiki, setProposeWiki] = useState(true);
  const [saving, setSaving] = useState(false);
  const [status, setStatus] = useState<{ message: string; ok: boolean } | null>(null);
  const [toast, setToast] = useState<{ message: string; ok: boolean } | null>(null);
  const [termDraft, setTermDraft] = useState("");
  const abortRef = useRef<AbortController | null>(null);
  const toastTimer = useRef<number | null>(null);
  const t = (key: keyof typeof copy.zh) => copy[lang][key];
  const timeouts = timeoutValues(prefs.command_timeout_budget);

  function toggleLang() {
    setLang((current) => {
      const next = current === "zh" ? "en" : "zh";
      window.localStorage.setItem("tm-lang", next);
      return next;
    });
  }

  function showToast(message: string, ok = true) {
    setToast({ message, ok });
    if (toastTimer.current) window.clearTimeout(toastTimer.current);
    toastTimer.current = window.setTimeout(() => setToast(null), 4500);
  }

  function updatePrefs(patch: Partial<Preferences>) {
    setPrefs((current) => ({ ...current, ...patch }));
  }

  function toggleListValue(key: "exemptions" | "agents", value: string) {
    const current = new Set(prefs[key]);
    if (current.has(value)) current.delete(value);
    else current.add(value);
    updatePrefs({ [key]: Array.from(current) } as Partial<Preferences>);
  }

  function addCustomTerm() {
    const term = termDraft.trim();
    if (!term) return;
    if (prefs.custom_terms.includes(term)) {
      showToast(`${t("duplicate")}：${term}`, false);
      setTermDraft("");
      return;
    }
    updatePrefs({ custom_terms: [...prefs.custom_terms, term] });
    setTermDraft("");
  }

  function removeCustomTerm(index: number) {
    updatePrefs({ custom_terms: prefs.custom_terms.filter((_, itemIndex) => itemIndex !== index) });
  }

  function updateTimeout(key: keyof ReturnType<typeof timeoutValues>, value: number) {
    updatePrefs({ command_timeout_budget: toTimeoutString({ ...timeouts, [key]: value }) });
  }

  async function fetchSettings() {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    try {
      const response = await fetch("/api/settings/preferences", { signal: controller.signal });
      const payload = (await response.json()) as SettingsData;
      if (!payload.ok) throw new Error(String(payload.error || "偏好数据加载失败"));
      setPath(String(payload.path || path));
      setPrefs(normalizePrefs(payload.preferences));
      setDefaults(normalizePrefs(payload.defaults));
    } catch (exc) {
      if ((exc as Error).name === "AbortError") return;
      setStatus({ message: `${t("error")}：${(exc as Error).message}`, ok: false });
    }
  }

  async function saveSettings(event: FormEvent) {
    event.preventDefault();
    setSaving(true);
    setStatus({ message: t("saving"), ok: true });
    try {
      const response = await fetch("/api/settings/preferences", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ preferences: prefs, propose_wiki: proposeWiki }),
      });
      const payload = await response.json();
      if (!payload.ok) throw new Error(String(payload.error || "保存失败"));
      const nextPrefs = normalizePrefs(payload.preferences || prefs);
      setPrefs(nextPrefs);
      const proposal = payload.wiki_proposal || {};
      const suffix = proposal.commit_sha ? `，${t("synced")} ${proposal.commit_sha}` : `，${t("syncSubmitted")}`;
      const message = `${t("savedLocal")}${suffix}`;
      setStatus({ message, ok: true });
      showToast(message, true);
    } catch (exc) {
      const message = `${t("error")}：${(exc as Error).message}`;
      setStatus({ message, ok: false });
      showToast(message, false);
    } finally {
      setSaving(false);
    }
  }

  function resetDefaults() {
    if (!window.confirm("确定要将所有设置恢复为默认值吗？此操作需要点击“保存设置”才会写入数据库。")) return;
    setPrefs(defaults);
    setStatus({ message: t("resetPending"), ok: true });
  }

  useEffect(() => {
    if (initialData.loading) fetchSettings();
    return () => {
      abortRef.current?.abort();
      if (toastTimer.current) window.clearTimeout(toastTimer.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <DashboardShell active="/settings" lang={lang} onToggleLang={toggleLang} tagline={t("tagline")} badge={t("badge")}>
      <main className="relative z-10 mx-auto max-w-6xl px-5 py-6">
        <DashboardCard>
          <div className="min-w-0">
            <div className="mb-2 inline-flex items-center gap-2 text-sm font-medium text-tm-tertiary">
              <Settings2 size={16} className="text-tm-accent" />
              <span>{t("steward")}</span>
            </div>
            <h1 className="text-2xl font-extrabold leading-9 text-tm-primary">{t("title")}</h1>
            <p className="mt-2 max-w-3xl text-sm leading-6 text-tm-secondary">{t("intro")}</p>
          </div>
        </DashboardCard>

        {initialData.loading && (
          <div className="mb-4 rounded-2xl border border-tm-info-border bg-tm-info-bg p-4 text-sm text-tm-info">
            {t("loading")}
          </div>
        )}

        <form className="space-y-5" onSubmit={saveSettings}>
          <DashboardCard icon={<MessageCircle size={20} />} title={t("depth")}>
            <div className="grid gap-3 md:grid-cols-4">
              {depthOptions.map(([value, title, subtitle, example]) => {
                const active = prefs.communication_depth === value;
                return (
                  <motion.button
                    layout
                    key={value}
                    type="button"
                    onClick={() => updatePrefs({ communication_depth: value })}
                    className={cx(
                      "min-h-32 rounded-xl border p-4 text-left transition-colors",
                      active ? "border-tm-accent bg-tm-warn-bg" : "border-tm-border bg-tm-card-alt hover:border-tm-accent",
                    )}
                  >
                    <div className="mb-2 text-lg font-extrabold text-tm-primary">{active ? "◉" : "○"} {title}</div>
                    <div className="text-sm font-semibold text-tm-secondary">{subtitle}</div>
                    <div className="mt-4 text-xs leading-5 text-tm-tertiary">{example}</div>
                  </motion.button>
                );
              })}
            </div>
          </DashboardCard>

          <DashboardCard icon={<BadgeCheck size={20} />} title={t("exemptions")}>
            <div className="flex flex-wrap gap-2">
              {exemptionOptions.map(([value, label]) => (
                <Chip key={value} active={prefs.exemptions.includes(value)} onClick={() => toggleListValue("exemptions", value)}>
                  {label}
                </Chip>
              ))}
            </div>
            <label className="mt-5 block text-sm font-semibold text-tm-primary">{t("customTerms")}</label>
            <div className="mt-2 flex flex-wrap items-center gap-2 rounded-2xl border border-tm-border bg-tm-card-alt p-3">
              <AnimatePresence>
                {prefs.custom_terms.map((term, index) => (
                  <motion.span
                    key={term}
                    layout
                    initial={{ opacity: 0, y: 4 }}
                    animate={{ opacity: 1, y: 0 }}
                    exit={{ opacity: 0, y: -4 }}
                    className="inline-flex items-center gap-2 rounded-full border border-tm-border bg-tm-card px-3 py-1.5 text-sm text-tm-secondary"
                  >
                    {term}
                    <button type="button" onClick={() => removeCustomTerm(index)} className="text-tm-tertiary hover:text-tm-fail" aria-label={`remove ${term}`}>
                      <X size={14} />
                    </button>
                  </motion.span>
                ))}
              </AnimatePresence>
              <input
                value={termDraft}
                onChange={(event) => setTermDraft(event.target.value)}
                onBlur={addCustomTerm}
                onKeyDown={(event) => {
                  if (event.key === "Enter") {
                    event.preventDefault();
                    addCustomTerm();
                  }
                }}
                className="min-w-52 flex-1 bg-transparent px-2 py-1.5 text-sm text-tm-primary outline-none"
                placeholder={t("customPlaceholder")}
              />
            </div>
          </DashboardCard>

          <section className="grid gap-5 lg:grid-cols-2">
            <DashboardCard title={t("progressive")}>
              <div className="flex items-start justify-between gap-4">
                <div>
                  <p className="text-sm leading-6 text-tm-secondary">{t("progressiveDesc")}</p>
                </div>
                <button
                  type="button"
                  onClick={() => updatePrefs({ progressive_term_frequency: !prefs.progressive_term_frequency })}
                  className={cx(
                    "relative h-8 w-14 shrink-0 rounded-full border transition-colors",
                    prefs.progressive_term_frequency ? "border-tm-accent bg-tm-accent" : "border-tm-border-strong bg-tm-card-alt",
                  )}
                  aria-pressed={prefs.progressive_term_frequency}
                >
                  <motion.span
                    layout
                    className="absolute top-1 h-5 w-5 rounded-full bg-tm-card shadow"
                    animate={{ left: prefs.progressive_term_frequency ? 28 : 4 }}
                    transition={{ type: "spring", stiffness: 420, damping: 30 }}
                  />
                </button>
              </div>
              <div className="mt-4 rounded-2xl border border-tm-warn-border bg-tm-warn-bg p-4 text-sm leading-6 text-tm-warn">
                {t("progressiveNote")}
              </div>
            </DashboardCard>

            <DashboardCard icon={<Bot size={20} />} title={t("agents")}>
              <div className="flex flex-wrap gap-2">
                {agentOptions.map((agent) => (
                  <Chip key={agent} active={prefs.agents.includes(agent)} onClick={() => toggleListValue("agents", agent)}>
                    {agent}
                  </Chip>
                ))}
              </div>
            </DashboardCard>
          </section>

          <section className="grid gap-5 lg:grid-cols-2">
            <DashboardCard title={t("workflow")}>
              <textarea
                value={prefs.model_workflow}
                onChange={(event) => updatePrefs({ model_workflow: event.target.value })}
                rows={5}
                className="w-full resize-y rounded-2xl border border-tm-border-strong bg-tm-card p-4 text-sm leading-6 text-tm-primary outline-none focus:border-tm-accent"
              />
            </DashboardCard>

            <DashboardCard icon={<TimerReset size={20} />} title={t("timeout")}>
              <div className="space-y-4">
                {timeoutRows.map(([key, label, min, max]) => (
                  <div key={key} className="grid grid-cols-[1fr_auto] items-center gap-3 rounded-xl border border-tm-border bg-tm-card-alt p-3">
                    <label className="min-w-0">
                      <div className="mb-1 text-sm font-semibold text-tm-primary">{label}</div>
                      <input
                        type="range"
                        min={min}
                        max={max}
                        value={timeouts[key]}
                        onChange={(event) => updateTimeout(key, Number(event.target.value))}
                        className="w-full accent-tm-accent"
                      />
                    </label>
                    <span className="rounded-full border border-tm-border bg-tm-card px-3 py-1 text-sm font-semibold text-tm-secondary">
                      {timeouts[key]} 秒
                    </span>
                  </div>
                ))}
              </div>
            </DashboardCard>
          </section>

          <DashboardCard>
            <div className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
              <label className="flex items-center gap-3 text-sm font-semibold text-tm-primary">
                <input
                  type="checkbox"
                  checked={proposeWiki}
                  onChange={(event) => setProposeWiki(event.target.checked)}
                  className="h-4 w-4 accent-tm-accent"
                />
                <span>{t("proposeWiki")}</span>
              </label>
              <div className="flex flex-wrap gap-2">
                <button type="button" onClick={resetDefaults} className="inline-flex items-center gap-2 rounded-md border border-tm-border-strong bg-tm-card-alt px-4 py-2 text-sm font-semibold text-tm-secondary hover:bg-tm-overlay">
                  <RotateCcw size={16} />
                  {t("reset")}
                </button>
                <button type="submit" disabled={saving} className="inline-flex items-center gap-2 rounded-md bg-tm-accent px-4 py-2 text-sm font-semibold text-tm-primary hover:bg-tm-accent-hi disabled:opacity-50">
                  {saving ? <Loader2 size={16} className="animate-spin" /> : <Save size={16} />}
                  {t("save")}
                </button>
              </div>
            </div>
            {status && (
              <div className={cx("mt-3 flex items-center gap-2 text-sm", status.ok ? "text-tm-ok" : "text-tm-fail")}>
                {status.ok ? <Check size={16} /> : <X size={16} />}
                {status.message}
              </div>
            )}
          </DashboardCard>

          <DashboardCard>
            <details>
              <summary className="cursor-pointer text-sm font-semibold text-tm-primary">{t("detail")}</summary>
              <pre className="mt-4 max-h-[52vh] overflow-auto whitespace-pre-wrap rounded-2xl border border-tm-border bg-tm-card-alt p-4 text-xs leading-5 text-tm-secondary">
                {JSON.stringify({ path, preferences: prefs, defaults }, null, 2)}
              </pre>
            </details>
          </DashboardCard>
        </form>
      </main>
      <StatusToast toast={toast} />
    </DashboardShell>
  );
}

createRoot(document.getElementById("root")!).render(<App />);
