import { Globe2 } from "lucide-react";
import { motion, useReducedMotion } from "motion/react";
import type { ReactNode } from "react";

import { ParticleField } from "../ParticleField";

export type DashboardLang = "zh" | "en";

export const dashboardNavItems = [
  { href: "/start", label: { zh: "开始", en: "Start" } },
  { href: "/digest", label: { zh: "今日待确认", en: "Review" } },
  { href: "/ledger", label: { zh: "记账", en: "Ledger" } },
  { href: "/health", label: { zh: "运行检查", en: "Health" } },
  { href: "/quality", label: { zh: "记忆质量", en: "Quality" } },
  { href: "/canvas", label: { zh: "项目进展", en: "Projects" } },
  { href: "/self-evolution", label: { zh: "自我进化", en: "Evolution" } },
  { href: "/agent-tools", label: { zh: "AI 连接", en: "AI Tools" } },
  { href: "/settings", label: { zh: "偏好设置", en: "Settings" } },
] as const;

function cx(...items: Array<string | false | null | undefined>) {
  return items.filter(Boolean).join(" ");
}

function dashboardVersionLabel() {
  const node = document.getElementById("tm-dashboard-meta");
  const raw = node?.textContent?.trim();
  if (raw && !raw.includes("__GIT_SHA__")) {
    try {
      const meta = JSON.parse(raw) as { git_sha?: string; version?: string };
      const value = meta.git_sha || meta.version || "";
      if (value) return value.length > 12 ? value.slice(0, 7) : value;
    } catch {
      return raw.length > 12 ? raw.slice(0, 7) : raw;
    }
  }
  const legacy = document.getElementById("sha-pill")?.textContent?.trim();
  if (legacy && !legacy.includes("__GIT_SHA__")) return legacy.length > 12 ? legacy.slice(0, 7) : legacy;
  return "";
}

export function DashboardHeader({
  active,
  lang,
  onToggleLang,
  tagline,
  badge,
}: {
  active: string;
  lang: DashboardLang;
  onToggleLang: () => void;
  tagline?: string;
  badge?: string;
}) {
  const reduceMotion = useReducedMotion();
  const activeTransition = reduceMotion
    ? { duration: 0 }
    : { type: "spring" as const, stiffness: 420, damping: 34, mass: 0.72 };
  const versionLabel = dashboardVersionLabel() || badge;

  return (
    <header className="sticky top-0 z-30 border-b border-tm-border-divider bg-tm-bg/95 backdrop-blur">
      <div className="mx-auto flex max-w-6xl items-center justify-between gap-4 px-6 py-4">
        <a href="/start" className="flex w-[220px] shrink-0 select-none items-center gap-3">
          <img src="/static/tiger/tigerlogo.png" alt="" className="h-10 w-10" />
          <span>
            <span className="block text-base font-extrabold leading-none text-tm-primary">TigerMemory</span>
            <span className="mt-0.5 block text-xs text-tm-tertiary">
              {tagline || (lang === "zh" ? "你的 AI 第二大脑" : "Your AI second brain")}
            </span>
          </span>
        </a>

        <nav className="hidden flex-1 items-center justify-center gap-1 md:flex">
          {dashboardNavItems.map((item) => {
            const selected = item.href === active;
            return (
              <a
                key={item.href}
                href={item.href}
                className={cx(
                  "relative rounded-xl px-2.5 py-2 text-[13px] leading-5 whitespace-nowrap transition-colors",
                  selected ? "font-bold text-tm-inverse" : "text-tm-secondary hover:bg-tm-card-alt",
                )}
              >
                {selected && (
                  <motion.span
                    layoutId="tm-dashboard-nav-active"
                    className="absolute inset-0 rounded-xl bg-tm-accent shadow-[0_2px_6px_rgba(200,165,96,0.18)]"
                    transition={activeTransition}
                  />
                )}
                <span className="relative z-10">{item.label[lang]}</span>
              </a>
            );
          })}
        </nav>

        <div className="flex w-[220px] shrink-0 items-center justify-end gap-2">
          <button
            type="button"
            onClick={onToggleLang}
            className="inline-flex items-center gap-1 rounded-full bg-tm-card-alt px-2 py-1 text-xs font-semibold text-tm-tertiary hover:text-tm-secondary"
            aria-label="Toggle language"
          >
            <Globe2 size={13} />
            {lang === "zh" ? "中" : "EN"}
          </button>
          {versionLabel && (
            <code className="min-w-[4.75rem] rounded-full bg-tm-card-alt px-2 py-1 text-center text-xs text-tm-tertiary">
              {versionLabel}
            </code>
          )}
        </div>
      </div>
    </header>
  );
}

export function DashboardShell({
  active,
  lang,
  onToggleLang,
  tagline,
  badge,
  children,
}: {
  active: string;
  lang: DashboardLang;
  onToggleLang: () => void;
  tagline?: string;
  badge?: string;
  children: ReactNode;
}) {
  return (
    <div className="relative min-h-screen bg-tm-bg text-tm-primary">
      <ParticleField />
      <img
        src="/static/tiger/tigerlogo.png"
        alt=""
        aria-hidden="true"
        className="pointer-events-none fixed -bottom-[330px] -left-[180px] z-0 w-[min(1040px,90vw)] select-none opacity-10"
      />
      <div className="pointer-events-none fixed right-[-48px] top-24 z-0 h-[130px] w-[220px] scale-x-[-1] bg-[url('/static/tiger/tigermemory_tiger_stripes_bg.svg')] bg-contain bg-center bg-no-repeat opacity-15" />
      <DashboardHeader active={active} lang={lang} onToggleLang={onToggleLang} tagline={tagline} badge={badge} />
      {children}
    </div>
  );
}

export function DashboardCard({
  icon,
  title,
  count,
  children,
  className,
}: {
  icon?: ReactNode;
  title?: string;
  count?: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <motion.section
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.3, ease: [0.22, 1, 0.36, 1] }}
      className={cx(
        "mb-5 rounded-2xl border border-tm-border bg-tm-card p-4 shadow-[0_1px_2px_rgba(31,29,27,0.04),0_12px_32px_rgba(168,123,34,0.06)]",
        className,
      )}
    >
      {title && (
        <h2 className="mb-3 flex items-center gap-2 text-lg font-semibold text-tm-primary">
          {icon && <span className="text-tm-accent">{icon}</span>}
          <span>{title}</span>
          {count && (
            <span className="ml-auto rounded-full border border-tm-border-divider bg-tm-card-alt px-3 py-1 text-xs text-tm-secondary">
              {count}
            </span>
          )}
        </h2>
      )}
      {children}
    </motion.section>
  );
}
