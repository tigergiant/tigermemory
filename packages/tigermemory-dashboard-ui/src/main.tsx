import {
  Bot,
  Check,
  ChevronLeft,
  ChevronRight,
  CircleHelp,
  Code2,
  Database,
  ExternalLink,
  Gauge,
  KeyRound,
  Laptop,
  Layers3,
  LayoutDashboard,
  Loader2,
  SearchCheck,
  Settings2,
  ShieldCheck,
  Sparkles,
} from "lucide-react";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import React, { useMemo, useState } from "react";
import { createRoot } from "react-dom/client";

import { DashboardShell } from "./components/DashboardShell";
import "./styles.css";

type Lang = "zh" | "en";
type StepId = "intro" | "mode" | "style" | "llm" | "agent" | "pages" | "finish";
type DepthId = "A" | "B" | "C" | "D";
type AnyRecord = Record<string, unknown>;

type SoftwareItem = {
  id: string;
  label: string;
  installed: boolean;
  support: "supported" | "planned" | string;
  target?: string;
  detected_signals?: string[];
};

type StartData = {
  profile?: string;
  preferences?: { communication_depth?: string };
  llm_status?: AnyRecord;
  agent_connect?: {
    ok?: boolean;
    targets?: AnyRecord[];
    installed_agents?: SoftwareItem[];
    software_scan?: AnyRecord;
  };
  generated_at?: string;
  commands?: { label?: string; command?: string }[];
};

const copy = {
  zh: {
    brandSub: "你的 AI 第二大脑",
    navStart: "开始",
    navDigest: "今日待确认",
    navMemory: "记账",
    navHealth: "运行检查",
    navQuality: "记忆质量",
    navCanvas: "项目进展",
    navEvolution: "自我进化",
    navAgent: "AI 连接",
    navSettings: "偏好设置",
    profile: "当前模式",
    localFirst: "本地证据优先",
    stepIntro: "认识",
    stepMode: "模式",
    stepStyle: "回复",
    stepLlm: "模型",
    stepAgent: "Agent",
    stepPages: "页面",
    stepFinish: "完成",
    previous: "上一步",
    next: "下一步",
    openDashboard: "进入控制台",
    startCheck: "先看运行检查",
    introEyebrow: "欢迎使用",
    introTitle: "先把你的本地记忆库接起来",
    introLead:
      "TigerMemory 把 Wiki、证据、规则和问答入口放在一个可维护的本地资料库里。你保留最终控制权，AI 先查证据，再给建议。",
    introPillWiki: "知识库",
    introPillAgent: "AI 助手",
    introPillEvidence: "证据",
    introPillHuman: "你",
    introWiki: "长期知识、项目说明、个人规则",
    introAgent: "先读规则，再搜索，再行动",
    introEvidence: "先查清楚，再回答",
    introHuman: "保留最终批准权",
    introCta: "几步设置后，你的 AI 就能按你的规则使用 TigerMemory。",
    modeEyebrow: "选择运行方式",
    modeTitle: "普通版还是高级版",
    modeLead: "第一次使用建议先选 local + LLM。它不需要 Docker 或 WSL，但能直接体验本地 Wiki 和模型问答。",
    modeLocalTitle: "普通版 / local",
    modeLocalDesc: "Python + Git + Markdown + SQLite。适合第一次安装、单机使用和轻量知识管理。",
    modeHybridTitle: "高级版 / hybrid",
    modeHybridDesc: "接入实时记忆、MCP、多 IDE 和远程网关。适合把记忆系统接进日常工作流。",
    modeRecommended: "现在推荐",
    modeAdvanced: "稍后开启",
    styleEyebrow: "回复偏好",
    styleTitle: "选择 AI 回答你的方式",
    styleLead: "这不是永久锁死。你可以先选一个舒服的风格，以后在偏好设置里随时调整。",
    styleSave: "保存回复方式",
    styleSaved: "已保存",
    styleAName: "简洁结论",
    styleAChip: "最快",
    styleAAnswer: "结论：可以继续。关键风险是 1998 正式服务不要被测试分支污染。",
    styleANote: "适合你只想知道能不能放行。",
    styleBName: "结论 + 理由",
    styleBChip: "推荐",
    styleBAnswer:
      "结论：可以继续，但先保留回滚点。原因是本轮只改 /start 页面，正式数据页不动；验证要看页面、接口和测试三块。",
    styleBNote: "适合大多数日常开发和验收。",
    styleCName: "完整过程",
    styleCChip: "详细",
    styleCAnswer:
      "我会先说明当前状态，再列已改内容、验证结果、剩余风险和下一步。涉及服务或 Git，会给出可复查的路径和命令结果。",
    styleCNote: "适合排障、架构设计和交接。",
    styleDName: "教学解释",
    styleDChip: "入门",
    styleDAnswer:
      "我会用更白话的方式解释：这个页面相当于 TigerMemory 的新手向导，它负责帮你把模型、工具和使用习惯先设置好。",
    styleDNote: "适合刚接触 AI 工具或需要边学边做。",
    llmEyebrow: "连接 LLM",
    llmTitle: "连接一个你自己的模型",
    llmLead:
      "推荐先用 DeepSeek。API Key 只保存到本机 runtime 配置，不上传，也不会写进 Git。保存前会先做一次短测试。",
    llmProvider: "模型服务",
    llmApiKey: "API Key",
    llmBaseUrl: "接口地址",
    llmDailyModel: "日常模型",
    llmAdminModel: "管理模型",
    llmTestSave: "测试并保存",
    llmTesting: "正在测试连接...",
    llmSaved: "连接通过，已保存",
    llmFailed: "连接失败，请检查 Key、地址或模型名",
    llmPreviewTitle: "TigerMemory 会这样使用模型",
    llmDaily: "日常问答",
    llmAdmin: "Wiki 管理",
    llmLocal: "密钥位置",
    llmBadgeDaily: "常用",
    llmBadgeAdmin: "审核",
    llmBadgeLocal: "本机",
    llmDailyDesc: "deepseek-v4-flash",
    llmAdminDesc: "deepseek-v4-pro",
    llmLocalDesc: "只写入本机 runtime 配置，不进 Git。",
    agentEyebrow: "连接 AI 工具",
    agentTitle: "让你的 AI 知道怎么使用 TigerMemory",
    agentLead:
      "这里会扫描常见 AI 编程工具。已支持的工具可以写入项目规则；暂不支持的工具会先显示为计划接入，不会乱改全局配置。",
    agentApply: "应用项目级规则",
    agentApplying: "正在写入规则...",
    agentApplied: "已写入项目规则",
    agentDetected: "已检测到",
    agentSupported: "可一键应用",
    agentPlanned: "计划支持",
    agentMissing: "未检测到",
    agentMissingToggle: "展开全部",
    agentNoDetected: "暂未检测到可接入工具，仍可稍后手动配置。",
    agentSignals: "检测依据",
    agentManageLink: "完整管理 →",
    pagesEyebrow: "熟悉控制台",
    pagesTitle: "这些页面分别做什么",
    pagesLead: "不用一次全部学会。先记住三个入口：今日待确认、运行检查、AI 连接。",
    pageDigestTitle: "今日待确认",
    pageDigestDesc: "处理需要你确认的记忆、Wiki 提案和归档建议。",
    pageHealthTitle: "运行检查",
    pageHealthDesc: "看服务、缓存、端口和后台任务是否正常。",
    pageQualityTitle: "记忆质量",
    pageQualityDesc: "看分流、失败问题和自然语言问答质量。",
    pageCanvasTitle: "项目进展",
    pageCanvasDesc: "查看项目星图和活跃模块。",
    pageAgentTitle: "AI 连接",
    pageAgentDesc: "检查 Codex、Claude、Cursor 等工具是否接上。",
    pageSettingsTitle: "偏好设置",
    pageSettingsDesc: "调整回复详略、语言和本地运行偏好。",
    finishEyebrow: "完成",
    finishTitle: "你已经可以开始使用 TigerMemory",
    finishLead: "先从本地 Wiki + LLM 开始。等用顺手了，再接入更多设备、实时记忆和高级工作流。",
    finishLocal: "本地资料库可读",
    finishLlm: "模型配置",
    finishAgent: "AI 工具规则",
    finishStyle: "回复偏好",
    finishReady: "已准备",
    finishTodo: "稍后补齐",
    commandTitle: "常用命令",
    copied: "已复制",
    errorGeneric: "操作失败，请稍后重试",
    language: "语言",
  },
  en: {
    brandSub: "Your second brain for AI",
    navStart: "Start",
    navDigest: "Review",
    navMemory: "Ledger",
    navHealth: "Health",
    navQuality: "Quality",
    navCanvas: "Projects",
    navEvolution: "Evolution",
    navAgent: "AI Tools",
    navSettings: "Settings",
    profile: "Mode",
    localFirst: "Local evidence first",
    stepIntro: "Intro",
    stepMode: "Mode",
    stepStyle: "Style",
    stepLlm: "Model",
    stepAgent: "Agent",
    stepPages: "Pages",
    stepFinish: "Done",
    previous: "Back",
    next: "Next",
    openDashboard: "Open dashboard",
    startCheck: "Run health check",
    introEyebrow: "Welcome",
    introTitle: "Connect your local memory base first",
    introLead:
      "TigerMemory keeps your Wiki, evidence, rules, and answer workflow in a maintainable local knowledge base. You stay in control while AI checks evidence before acting.",
    introPillWiki: "Knowledge",
    introPillAgent: "AI agents",
    introPillEvidence: "Evidence",
    introPillHuman: "You",
    introWiki: "Long-term knowledge, project notes, personal rules",
    introAgent: "Read rules, search, then act",
    introEvidence: "Check evidence before answering",
    introHuman: "Keep final approval with you",
    introCta: "After a few setup steps, your AI tools can use TigerMemory with your rules.",
    modeEyebrow: "Choose a mode",
    modeTitle: "Basic or advanced",
    modeLead: "For a first install, use local + LLM. It needs no Docker or WSL and still gives you local Wiki search plus model answers.",
    modeLocalTitle: "Basic / local",
    modeLocalDesc: "Python + Git + Markdown + SQLite. Best for first install, single-device use, and lightweight knowledge work.",
    modeHybridTitle: "Advanced / hybrid",
    modeHybridDesc: "Adds realtime memory, MCP, multiple IDEs, and remote gateways. Best when TigerMemory becomes part of daily work.",
    modeRecommended: "Recommended now",
    modeAdvanced: "Enable later",
    styleEyebrow: "Answer preference",
    styleTitle: "Choose how AI should answer you",
    styleLead: "This is not permanent. Pick a comfortable style now and change it later in Settings.",
    styleSave: "Save answer style",
    styleSaved: "Saved",
    styleAName: "Concise",
    styleAChip: "Fastest",
    styleAAnswer: "Decision: continue. Main risk: keep the official 1998 service isolated from test branches.",
    styleANote: "Good when you only need a go/no-go answer.",
    styleBName: "Decision + reason",
    styleBChip: "Recommended",
    styleBAnswer:
      "Decision: continue, but keep a rollback point. This round only changes /start, not production data pages; verify page, API, and tests.",
    styleBNote: "Best for most daily development reviews.",
    styleCName: "Full process",
    styleCChip: "Detailed",
    styleCAnswer:
      "I will summarize state, changes, checks, remaining risks, and next steps. For services or Git, I will include reproducible paths and results.",
    styleCNote: "Best for debugging, architecture, and handoff.",
    styleDName: "Learning mode",
    styleDChip: "Beginner",
    styleDAnswer:
      "I will explain in plain language: this page is TigerMemory's setup guide. It helps configure your model, tools, and answer habits first.",
    styleDNote: "Best when learning AI tools while doing the work.",
    llmEyebrow: "Connect LLM",
    llmTitle: "Connect a model you control",
    llmLead:
      "DeepSeek is recommended. Your API key is saved only to local runtime config, never uploaded and never written to Git. TigerMemory tests the connection before saving.",
    llmProvider: "Provider",
    llmApiKey: "API Key",
    llmBaseUrl: "Endpoint",
    llmDailyModel: "Daily model",
    llmAdminModel: "Admin model",
    llmTestSave: "Test and save",
    llmTesting: "Testing connection...",
    llmSaved: "Connection passed and saved",
    llmFailed: "Connection failed. Check your key, endpoint, or model name.",
    llmPreviewTitle: "How TigerMemory will use models",
    llmDaily: "Daily answers",
    llmAdmin: "Wiki management",
    llmLocal: "Secret storage",
    llmBadgeDaily: "Daily",
    llmBadgeAdmin: "Review",
    llmBadgeLocal: "Local",
    llmDailyDesc: "deepseek-v4-flash",
    llmAdminDesc: "deepseek-v4-pro",
    llmLocalDesc: "Stored in local runtime config only, not Git.",
    agentEyebrow: "Connect AI tools",
    agentTitle: "Teach your AI tools how to use TigerMemory",
    agentLead:
      "This step scans common AI coding tools. Supported tools can receive project rules; planned tools are shown without changing global settings.",
    agentApply: "Apply project rules",
    agentApplying: "Writing rules...",
    agentApplied: "Project rules applied",
    agentDetected: "Detected",
    agentSupported: "Ready to apply",
    agentPlanned: "Planned",
    agentMissing: "Not detected",
    agentMissingToggle: "Expand all",
    agentNoDetected: "No connectable tools detected yet. You can configure them later.",
    agentSignals: "Signals",
    agentManageLink: "Full management →",
    pagesEyebrow: "Meet the dashboard",
    pagesTitle: "What each page is for",
    pagesLead: "You do not need to learn everything now. Start with Review, Health, and AI Tools.",
    pageDigestTitle: "Review",
    pageDigestDesc: "Approve memories, Wiki proposals, and archive suggestions.",
    pageHealthTitle: "Health",
    pageHealthDesc: "Check services, cache, ports, and background jobs.",
    pageQualityTitle: "Quality",
    pageQualityDesc: "Review routing, failure cases, and answer quality.",
    pageCanvasTitle: "Projects",
    pageCanvasDesc: "Inspect the project star map and active modules.",
    pageAgentTitle: "AI Tools",
    pageAgentDesc: "Check whether Codex, Claude, Cursor, and other tools are connected.",
    pageSettingsTitle: "Settings",
    pageSettingsDesc: "Adjust answer detail, language, and local preferences.",
    finishEyebrow: "Done",
    finishTitle: "TigerMemory is ready to use",
    finishLead: "Start with local Wiki + LLM. Add more devices, realtime memory, and advanced workflows later.",
    finishLocal: "Local knowledge base",
    finishLlm: "Model config",
    finishAgent: "AI tool rules",
    finishStyle: "Answer style",
    finishReady: "Ready",
    finishTodo: "Later",
    commandTitle: "Useful commands",
    copied: "Copied",
    errorGeneric: "Action failed. Try again later.",
    language: "Language",
  },
} as const;

const steps: StepId[] = ["intro", "mode", "style", "llm", "agent", "pages", "finish"];
const depthIds: DepthId[] = ["A", "B", "C", "D"];

const pageCards = [
  ["pageDigestTitle", "pageDigestDesc", "/digest", SearchCheck],
  ["pageHealthTitle", "pageHealthDesc", "/health", Gauge],
  ["pageQualityTitle", "pageQualityDesc", "/quality", Database],
  ["pageCanvasTitle", "pageCanvasDesc", "/canvas", Layers3],
  ["pageAgentTitle", "pageAgentDesc", "/agent-tools", Bot],
  ["pageSettingsTitle", "pageSettingsDesc", "/settings", Settings2],
] as const;

function parseInitialData(): StartData {
  const node = document.getElementById("tm-start-data");
  const text = node?.textContent?.trim();
  if (!text || text === "__TM_START_JSON__") return {};
  try {
    return JSON.parse(text) as StartData;
  } catch {
    return {};
  }
}

function initialLang(): Lang {
  const stored = window.localStorage.getItem("tm-lang");
  if (stored === "en" || stored === "zh") return stored;
  return window.navigator.language.toLowerCase().startsWith("en") ? "en" : "zh";
}

function classNames(...items: Array<string | false | null | undefined>) {
  return items.filter(Boolean).join(" ");
}

function safeText(value: unknown, fallback = ""): string {
  return typeof value === "string" && value ? value : fallback;
}

async function postJson(path: string, body: unknown) {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(String((data as AnyRecord).error || response.statusText));
  return data as AnyRecord;
}

function App() {
  const initialData = useMemo(parseInitialData, []);
  const reduceMotion = useReducedMotion();
  const [lang, setLang] = useState<Lang>(initialLang);
  const [step, setStep] = useState(0);
  const [depth, setDepth] = useState<DepthId>(
    depthIds.includes(initialData.preferences?.communication_depth as DepthId)
      ? (initialData.preferences?.communication_depth as DepthId)
      : "B",
  );
  const [styleStatus, setStyleStatus] = useState("");
  const [llmProvider, setLlmProvider] = useState("deepseek");
  const [apiKey, setApiKey] = useState("");
  const [baseUrl, setBaseUrl] = useState("https://api.deepseek.com/v1/chat/completions");
  const [dailyModel, setDailyModel] = useState("deepseek-v4-flash");
  const [adminModel, setAdminModel] = useState("deepseek-v4-pro");
  const [llmStatus, setLlmStatus] = useState("");
  const [agentStatus, setAgentStatus] = useState(initialData.agent_connect);
  const [agentBusy, setAgentBusy] = useState(false);
  const [agentMessage, setAgentMessage] = useState("");
  const [showMissing, setShowMissing] = useState(false);
  const [toast, setToast] = useState("");

  const t = (key: keyof typeof copy.zh) => copy[lang][key];
  const currentStep = steps[step];
  const profile = safeText(initialData.profile, "local");
  const software = agentStatus?.installed_agents ?? [];
  const detected = software.filter((item) => item.installed);
  const missing = software.filter((item) => !item.installed);
  const supportedDetected = detected.filter((item) => item.support === "supported");
  const defaultTargets =
    supportedDetected.map((item) => item.target).filter((value): value is string => Boolean(value)) ||
    [];

  function setLanguage(next: Lang) {
    window.localStorage.setItem("tm-lang", next);
    setLang(next);
  }

  function notify(message: string) {
    setToast(message);
    window.setTimeout(() => setToast(""), 1800);
  }

  async function copyCommand(command: string) {
    await window.navigator.clipboard?.writeText(command);
    notify(t("copied"));
  }

  async function saveDepth() {
    setStyleStatus("");
    try {
      await postJson("/api/settings/preferences", {
        preferences: { communication_depth: depth },
        propose_wiki: false,
      });
      setStyleStatus(t("styleSaved"));
    } catch {
      setStyleStatus(t("errorGeneric"));
    }
  }

  async function testAndSaveLlm() {
    setLlmStatus(t("llmTesting"));
    const payload = {
      provider: llmProvider,
      api_key: apiKey,
      base_url: baseUrl,
      model: dailyModel,
      admin_model: adminModel,
      test_connection: false,
    };
    try {
      const test = await postJson("/api/start/llm-test", payload);
      if (!test.ok) throw new Error(String(test.error || "llm test failed"));
      const saved = await postJson("/api/start/llm-config", payload);
      if (!saved.ok) throw new Error(String(saved.error || "llm save failed"));
      setLlmStatus(t("llmSaved"));
    } catch {
      setLlmStatus(t("llmFailed"));
    }
  }

  async function refreshAgents() {
    try {
      const response = await fetch("/api/start/agent-connect/status");
      const data = await response.json();
      if (data?.ok !== false) setAgentStatus(data);
    } catch {
      setAgentMessage(t("errorGeneric"));
    }
  }

  async function applyAgents() {
    setAgentBusy(true);
    setAgentMessage(t("agentApplying"));
    try {
      const data = await postJson("/api/start/agent-connect/apply", {
        targets: defaultTargets.length ? defaultTargets : ["codex", "claude-code", "cursor", "hooks"],
        dry_run: false,
      });
      setAgentStatus(data as StartData["agent_connect"]);
      setAgentMessage(t("agentApplied"));
      await refreshAgents();
    } catch {
      setAgentMessage(t("errorGeneric"));
    } finally {
      setAgentBusy(false);
    }
  }

  const panelMotion = reduceMotion
    ? { initial: false, animate: { opacity: 1 }, exit: { opacity: 0 } }
    : {
        initial: { opacity: 0, y: 10 },
        animate: { opacity: 1, y: 0 },
        exit: { opacity: 0, y: -6 },
        transition: { duration: 0.32, ease: [0.22, 1, 0.36, 1] },
      };

  return (
    <DashboardShell
      active="/start"
      lang={lang}
      onToggleLang={() => setLanguage(lang === "zh" ? "en" : "zh")}
      tagline={t("brandSub")}
    >
      <main className="tm-wizard" data-current-step={currentStep}>
        <section className="tm-wizard-card">
          <div className="tm-wizard-head">
            <div className="tm-step-dots" aria-label={`${step + 1} / ${steps.length}`}>
              {steps.map((id, idx) => (
                <button
                  key={id}
                  type="button"
                  className={classNames("tm-step-dot", idx === step && "active", idx < step && "done")}
                  onClick={() => setStep(idx)}
                  title={t(`step${id[0].toUpperCase()}${id.slice(1)}` as keyof typeof copy.zh)}
                />
              ))}
              <strong>
                {step + 1} / {steps.length}
              </strong>
            </div>
            <div className="tm-badges">
              <span>{t("profile")}: {profile}</span>
              <span>{t("localFirst")}</span>
            </div>
          </div>

          <AnimatePresence mode="wait">
            <motion.div key={`${currentStep}-${lang}`} className="tm-step-grid" {...panelMotion}>
              {currentStep === "intro" && (
                <IntroStep t={t} commands={initialData.commands ?? []} copyCommand={copyCommand} />
              )}
              {currentStep === "mode" && <ModeStep t={t} />}
              {currentStep === "style" && (
                <StyleStep t={t} depth={depth} setDepth={setDepth} saveDepth={saveDepth} status={styleStatus} />
              )}
              {currentStep === "llm" && (
                <LlmStep
                  t={t}
                  provider={llmProvider}
                  setProvider={setLlmProvider}
                  apiKey={apiKey}
                  setApiKey={setApiKey}
                  baseUrl={baseUrl}
                  setBaseUrl={setBaseUrl}
                  dailyModel={dailyModel}
                  setDailyModel={setDailyModel}
                  adminModel={adminModel}
                  setAdminModel={setAdminModel}
                  testAndSave={testAndSaveLlm}
                  status={llmStatus}
                />
              )}
              {currentStep === "agent" && (
                <AgentStep
                  t={t}
                  detected={detected}
                  missing={missing}
                  showMissing={showMissing}
                  setShowMissing={setShowMissing}
                  applyAgents={applyAgents}
                  busy={agentBusy}
                  message={agentMessage}
                />
              )}
              {currentStep === "pages" && <PagesStep t={t} />}
              {currentStep === "finish" && (
                <FinishStep
                  t={t}
                  llmReady={Boolean(llmStatus === t("llmSaved") || initialData.llm_status?.configured)}
                  agentReady={supportedDetected.length > 0}
                  depth={depth}
                />
              )}
            </motion.div>
          </AnimatePresence>

          <footer className="tm-wizard-actions">
            <button type="button" className="ghost" onClick={() => setStep(Math.max(0, step - 1))} disabled={step === 0}>
              <ChevronLeft size={16} />
              {t("previous")}
            </button>
            <p>{copy[lang][`step${steps[step][0].toUpperCase()}${steps[step].slice(1)}` as keyof typeof copy.zh]}</p>
            {step < steps.length - 1 ? (
              <button type="button" className="primary" onClick={() => setStep(Math.min(steps.length - 1, step + 1))}>
                {t("next")}
                <ChevronRight size={16} />
              </button>
            ) : (
              <a className="primary" href="/digest">
                {t("openDashboard")}
              </a>
            )}
          </footer>
        </section>
      </main>

      <AnimatePresence>
        {toast && (
          <motion.div
            className="tm-toast"
            initial={{ opacity: 0, y: 18 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: 18 }}
          >
            {toast}
          </motion.div>
        )}
      </AnimatePresence>
    </DashboardShell>
  );
}

function SectionTitle({ icon, eyebrow, title, lead }: { icon: React.ReactNode; eyebrow: string; title: string; lead: string }) {
  return (
    <div className="tm-section-title">
      <span className="tm-eyebrow">
        {icon}
        {eyebrow}
      </span>
      <h1 className="text-tm-primary">{title}</h1>
      <p>{lead}</p>
    </div>
  );
}

function IntroStep({
  t,
  commands,
  copyCommand,
}: {
  t: (key: keyof typeof copy.zh) => string;
  commands: { label?: string; command?: string }[];
  copyCommand: (command: string) => void;
}) {
  const cards = [
    ["introPillWiki", "introWiki", Database],
    ["introPillAgent", "introAgent", Bot],
    ["introPillEvidence", "introEvidence", SearchCheck],
    ["introPillHuman", "introHuman", ShieldCheck],
  ] as const;
  const commandByText = (needle: string) => commands.find((item) => item.command?.includes(needle));
  const usefulCommands =
    commands.length
      ? [
          commandByText("tm init") ?? commands[0],
          commandByText("tm ask --offline") ?? commandByText("tm search") ?? commands[1],
        ].filter(Boolean)
      : [
          { command: "tm init" },
          { command: 'tm ask --offline --query "agent behavior rules" --scope wiki' },
        ];
  return (
    <>
      <SectionTitle icon={<Sparkles size={16} />} eyebrow={t("introEyebrow")} title={t("introTitle")} lead={t("introLead")} />
      <div className="tm-visual-panel">
        <div className="tm-flow">
          {cards.map(([title, desc, Icon]) => (
            <div key={title} className="tm-flow-card">
              <Icon size={18} />
              <strong>{t(title)}</strong>
              <span>{t(desc)}</span>
            </div>
          ))}
        </div>
        <div className="tm-command-box">
          <strong>{t("commandTitle")}</strong>
          {usefulCommands.map((item, idx) => (
            <button key={`${item.command}-${idx}`} type="button" onClick={() => item.command && copyCommand(item.command)}>
              <code>{item.command}</code>
            </button>
          ))}
        </div>
      </div>
    </>
  );
}

function ModeStep({ t }: { t: (key: keyof typeof copy.zh) => string }) {
  return (
    <>
      <SectionTitle icon={<Laptop size={16} />} eyebrow={t("modeEyebrow")} title={t("modeTitle")} lead={t("modeLead")} />
      <div className="tm-mode-grid">
        <article className="tm-choice-card selected">
          <span>{t("modeRecommended")}</span>
          <h2 className="text-tm-primary">{t("modeLocalTitle")}</h2>
          <p>{t("modeLocalDesc")}</p>
          <div className="tm-stack">Python + Git + Markdown + SQLite</div>
        </article>
        <article className="tm-choice-card">
          <span>{t("modeAdvanced")}</span>
          <h2 className="text-tm-primary">{t("modeHybridTitle")}</h2>
          <p>{t("modeHybridDesc")}</p>
          <div className="tm-stack">MCP + realtime memory + gateway</div>
        </article>
      </div>
    </>
  );
}

function StyleStep({
  t,
  depth,
  setDepth,
  saveDepth,
  status,
}: {
  t: (key: keyof typeof copy.zh) => string;
  depth: DepthId;
  setDepth: (depth: DepthId) => void;
  saveDepth: () => void;
  status: string;
}) {
  const option = (id: DepthId) => ({
    name: t(`style${id}Name` as keyof typeof copy.zh),
    chip: t(`style${id}Chip` as keyof typeof copy.zh),
    answer: t(`style${id}Answer` as keyof typeof copy.zh),
    note: t(`style${id}Note` as keyof typeof copy.zh),
  });
  const current = option(depth);
  return (
    <>
      <SectionTitle icon={<CircleHelp size={16} />} eyebrow={t("styleEyebrow")} title={t("styleTitle")} lead={t("styleLead")} />
      <div className="tm-two-column">
        <div className="tm-option-list">
          {depthIds.map((id) => {
            const item = option(id);
            return (
              <button key={id} type="button" className={classNames("tm-option", depth === id && "active")} onClick={() => setDepth(id)}>
                <strong>{item.name}</strong>
                <span>{item.chip}</span>
                <small>{item.note}</small>
              </button>
            );
          })}
          <button type="button" className="primary compact" onClick={saveDepth}>
            {t("styleSave")}
          </button>
          {status && <p className="tm-status-line">{status}</p>}
        </div>
        <div className="tm-preview-card">
          <span>{current.chip}</span>
          <h2 className="text-tm-primary">{current.name}</h2>
          <p>{current.answer}</p>
          <small>{current.note}</small>
        </div>
      </div>
    </>
  );
}

function LlmStep(props: {
  t: (key: keyof typeof copy.zh) => string;
  provider: string;
  setProvider: (value: string) => void;
  apiKey: string;
  setApiKey: (value: string) => void;
  baseUrl: string;
  setBaseUrl: (value: string) => void;
  dailyModel: string;
  setDailyModel: (value: string) => void;
  adminModel: string;
  setAdminModel: (value: string) => void;
  testAndSave: () => void;
  status: string;
}) {
  const { t } = props;
  return (
    <>
      <SectionTitle icon={<KeyRound size={16} />} eyebrow={t("llmEyebrow")} title={t("llmTitle")} lead={t("llmLead")} />
      <div className="tm-two-column llm">
        <form className="tm-form" onSubmit={(event) => event.preventDefault()}>
          <label>
            {t("llmProvider")}
            <select value={props.provider} onChange={(event) => props.setProvider(event.target.value)}>
              <option value="deepseek">DeepSeek</option>
              <option value="openai_compatible">OpenAI Compatible</option>
            </select>
          </label>
          <label>
            {t("llmApiKey")}
            <input type="password" value={props.apiKey} onChange={(event) => props.setApiKey(event.target.value)} placeholder="sk-..." />
          </label>
          <label className="wide">
            {t("llmBaseUrl")}
            <input value={props.baseUrl} onChange={(event) => props.setBaseUrl(event.target.value)} />
          </label>
          <label>
            {t("llmDailyModel")}
            <input value={props.dailyModel} onChange={(event) => props.setDailyModel(event.target.value)} />
          </label>
          <label>
            {t("llmAdminModel")}
            <input value={props.adminModel} onChange={(event) => props.setAdminModel(event.target.value)} />
          </label>
          <button type="button" className="primary compact" onClick={props.testAndSave}>
            {props.status === t("llmTesting") && <Loader2 size={15} className="spin" />}
            {t("llmTestSave")}
          </button>
          {props.status && <p className="tm-status-line">{props.status}</p>}
        </form>
        <div className="tm-preview-card">
          <span>{t("llmPreviewTitle")}</span>
          <ModelRow title={t("llmDaily")} value={t("llmDailyDesc")} badge={t("llmBadgeDaily")} />
          <ModelRow title={t("llmAdmin")} value={t("llmAdminDesc")} badge={t("llmBadgeAdmin")} />
          <ModelRow title={t("llmLocal")} value={t("llmLocalDesc")} badge={t("llmBadgeLocal")} />
        </div>
      </div>
    </>
  );
}

function ModelRow({ title, value, badge }: { title: string; value: string; badge: string }) {
  return (
    <div className="tm-model-row">
      <strong>{title}</strong>
      <span>{value}</span>
      <em>{badge}</em>
    </div>
  );
}

function AgentStep({
  t,
  detected,
  missing,
  showMissing,
  setShowMissing,
  applyAgents,
  busy,
  message,
}: {
  t: (key: keyof typeof copy.zh) => string;
  detected: SoftwareItem[];
  missing: SoftwareItem[];
  showMissing: boolean;
  setShowMissing: (value: boolean) => void;
  applyAgents: () => void;
  busy: boolean;
  message: string;
}) {
  return (
    <>
      <SectionTitle icon={<Code2 size={16} />} eyebrow={t("agentEyebrow")} title={t("agentTitle")} lead={t("agentLead")} />
      <div className="tm-two-column agent">
        <div className="tm-agent-board">
          <div className="tm-agent-summary">
            <strong>
              {t("agentDetected")}: {detected.length}
            </strong>
            <span>
              {t("agentSupported")}: {detected.filter((item) => item.support === "supported").length}
            </span>
          </div>
          <button type="button" className="primary compact" onClick={applyAgents} disabled={busy}>
            {busy && <Loader2 size={15} className="spin" />}
            {t("agentApply")}
          </button>
          {message && <p className="tm-status-line">{message}</p>}
          <div className="tm-agent-list">
            {detected.length ? (
              detected.map((item) => <AgentRow key={item.id} item={item} t={t} />)
            ) : (
              <p className="tm-muted">{t("agentNoDetected")}</p>
            )}
          </div>
        </div>
        <div className="tm-preview-card">
          <span>{t("agentPlanned")}</span>
          <button type="button" className="tm-missing-toggle" onClick={() => setShowMissing(!showMissing)}>
            {t("agentMissingToggle")}
          </button>
          <AnimatePresence initial={false}>
            {showMissing && (
              <motion.div
                className="tm-missing-list"
                initial={{ height: 0, opacity: 0 }}
                animate={{ height: "auto", opacity: 1 }}
                exit={{ height: 0, opacity: 0 }}
              >
                {missing.map((item) => (
                  <AgentRow key={item.id} item={item} t={t} compact />
                ))}
              </motion.div>
            )}
          </AnimatePresence>
          <a className="tm-manage-link" href="/agent-tools">
            <ExternalLink size={13} />
            {t("agentManageLink")}
          </a>
        </div>
      </div>
    </>
  );
}

function AgentRow({ item, t, compact = false }: { item: SoftwareItem; t: (key: keyof typeof copy.zh) => string; compact?: boolean }) {
  const supported = item.support === "supported";
  return (
    <div className={classNames("tm-agent-row", compact && "compact")}>
      <div>
        <strong>{item.label}</strong>
        {!compact && item.detected_signals?.length ? (
          <small>
            {t("agentSignals")}: {item.detected_signals.slice(0, 2).join(" · ")}
          </small>
        ) : null}
      </div>
      <span className={classNames("tm-status-pill", supported ? "ok" : item.installed ? "planned" : "muted")}>
        {supported ? t("agentSupported") : item.installed ? t("agentPlanned") : t("agentMissing")}
      </span>
    </div>
  );
}

function PagesStep({ t }: { t: (key: keyof typeof copy.zh) => string }) {
  return (
    <>
      <SectionTitle icon={<LayoutDashboard size={16} />} eyebrow={t("pagesEyebrow")} title={t("pagesTitle")} lead={t("pagesLead")} />
      <div className="tm-page-grid">
        {pageCards.map(([title, desc, href, Icon]) => (
          <a key={href} className="tm-page-card" href={href}>
            <Icon size={18} />
            <strong>{t(title)}</strong>
            <span>{t(desc)}</span>
          </a>
        ))}
      </div>
    </>
  );
}

function FinishStep({
  t,
  llmReady,
  agentReady,
  depth,
}: {
  t: (key: keyof typeof copy.zh) => string;
  llmReady: boolean;
  agentReady: boolean;
  depth: DepthId;
}) {
  const checks = [
    [t("finishLocal"), true],
    [t("finishLlm"), llmReady],
    [t("finishAgent"), agentReady],
    [`${t("finishStyle")} ${depth}`, true],
  ] as const;
  return (
    <>
      <SectionTitle icon={<ShieldCheck size={16} />} eyebrow={t("finishEyebrow")} title={t("finishTitle")} lead={t("finishLead")} />
      <div className="tm-finish-grid">
        {checks.map(([label, ok]) => (
          <div key={label} className="tm-finish-card">
            <Check size={18} />
            <strong>{label}</strong>
            <span>{ok ? t("finishReady") : t("finishTodo")}</span>
          </div>
        ))}
        <a className="tm-final-link" href="/health">
          {t("startCheck")}
        </a>
        <a className="tm-final-link primary-link" href="/digest">
          {t("openDashboard")}
        </a>
      </div>
    </>
  );
}

createRoot(document.getElementById("root")!).render(<App />);
