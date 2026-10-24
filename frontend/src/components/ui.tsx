import type { ReactNode } from "react";
import { Link } from "react-router";
import { WarningCircle, CheckCircle, Info, XCircle, CircleNotch } from "@phosphor-icons/react";
import { ApiError } from "../services/api";

export function cx(...parts: (string | false | null | undefined)[]) {
  return parts.filter(Boolean).join(" ");
}

type Tone = "neutral" | "ok" | "warn" | "bad" | "info" | "accent";

const toneClass: Record<Tone, string> = {
  neutral: "bg-white/[0.06] text-fog-300 ring-white/10",
  ok: "bg-ok/10 text-ok ring-ok/25",
  warn: "bg-warn/10 text-warn ring-warn/25",
  bad: "bg-bad/10 text-bad ring-bad/25",
  info: "bg-info/10 text-info ring-info/25",
  accent: "bg-lilac-300/10 text-lilac-200 ring-lilac-300/25",
};

export function Badge({ tone = "neutral", children, className }: { tone?: Tone; children: ReactNode; className?: string }) {
  return (
    <span
      className={cx(
        "inline-flex items-center gap-1 whitespace-nowrap rounded-full px-2.5 py-0.5 text-[11px] font-medium ring-1 ring-inset",
        toneClass[tone],
        className,
      )}
    >
      {children}
    </span>
  );
}

const STATUS_TONE: Record<string, Tone> = {
  REPORTED: "neutral",
  TRIAGED: "info",
  EVIDENCE_GATHERED: "info",
  PLANS_PROPOSED: "info",
  VALIDATING: "info",
  VALIDATING_AGAIN: "info",
  REVISION_REQUIRED: "warn",
  AWAITING_APPROVAL: "accent",
  APPROVED: "ok",
  EXECUTED: "ok",
  RESOLVED: "ok",
  REJECTED: "neutral",
  ESCALATED: "bad",
  NO_SAFE_PLAN: "bad",
  FAILED: "bad",
  SUPERSEDED: "neutral",
  STALE: "warn",
  INVALID: "bad",
  DRAFT: "neutral",
  CORROBORATED: "bad",
  CONTRADICTORY: "warn",
  UNCORROBORATED: "warn",
  NOT_INDICATED: "ok",
};

export const humanize = (s: string) => s.replace(/_/g, " ").toLowerCase().replace(/^\w/, (c) => c.toUpperCase());

export function StatusBadge({ status }: { status: string }) {
  return <Badge tone={STATUS_TONE[status] ?? "neutral"}>{humanize(status)}</Badge>;
}

export function SeverityIcon({ severity, size = 18 }: { severity: string; size?: number }) {
  if (severity === "blocking" || severity === "error")
    return <XCircle size={size} weight="fill" className="shrink-0 text-bad" aria-label="Blocking" />;
  if (severity === "warning")
    return <WarningCircle size={size} weight="fill" className="shrink-0 text-warn" aria-label="Warning" />;
  if (severity === "success")
    return <CheckCircle size={size} weight="fill" className="shrink-0 text-ok" aria-label="Success" />;
  return <Info size={size} weight="fill" className="shrink-0 text-info" aria-label="Info" />;
}

export function Panel({ title, action, children, className, id }: {
  title?: ReactNode; action?: ReactNode; children: ReactNode; className?: string; id?: string;
}) {
  return (
    <section id={id} className={cx("panel min-w-0 p-5", className)}>
      {(title || action) && (
        <header className="mb-4 flex items-center justify-between gap-3">
          {title && <h2 className="font-display text-[15px] font-semibold tracking-tight text-fog-100">{title}</h2>}
          {action}
        </header>
      )}
      {children}
    </section>
  );
}

export function Button({ variant = "primary", className, children, loading, ...props }: React.ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "ghost" | "danger" | "ok"; loading?: boolean;
}) {
  const styles = {
    primary: "bg-fog-100 text-ink-950 hover:bg-white",
    ghost: "bg-white/[0.04] text-fog-100 ring-1 ring-inset ring-white/12 hover:bg-white/[0.08]",
    danger: "bg-bad/12 text-bad ring-1 ring-inset ring-bad/35 hover:bg-bad/20",
    ok: "bg-ok text-ink-950 hover:brightness-110",
  }[variant];
  return (
    <button
      {...props}
      disabled={props.disabled || loading}
      className={cx(
        "inline-flex h-10 items-center justify-center gap-2 whitespace-nowrap rounded-full px-5 text-sm font-medium transition duration-200 active:scale-[0.98] disabled:cursor-not-allowed disabled:opacity-45",
        styles,
        className,
      )}
    >
      {loading && <CircleNotch size={16} className="animate-spin motion-reduce:animate-none" aria-hidden />}
      {children}
    </button>
  );
}

export function ErrorState({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const message = error instanceof ApiError ? error.message : "Something went wrong.";
  return (
    <div role="alert" className="flex items-start gap-3 rounded-xl border border-bad/30 bg-bad/[0.06] p-4 text-sm text-fog-100">
      <XCircle size={20} weight="fill" className="mt-0.5 shrink-0 text-bad" />
      <div className="flex-1">
        <p>{message}</p>
        {onRetry && (
          <button onClick={onRetry} className="mt-2 text-xs font-medium text-lilac-200 underline underline-offset-4">
            Try again
          </button>
        )}
      </div>
    </div>
  );
}

export function InlineError({ error }: { error: unknown }) {
  if (!error) return null;
  const message = error instanceof ApiError ? error.message : "Request failed.";
  return (
    <p role="alert" className="flex items-start gap-2 text-sm text-bad">
      <XCircle size={16} weight="fill" className="mt-0.5 shrink-0" /> {message}
    </p>
  );
}

export function Skeleton({ className }: { className?: string }) {
  return <div className={cx("skeleton", className)} aria-hidden />;
}

export function LoadingBlock({ rows = 3 }: { rows?: number }) {
  return (
    <div className="space-y-3" aria-busy="true" aria-label="Loading">
      {Array.from({ length: rows }).map((_, i) => (
        <Skeleton key={i} className={i === 0 ? "h-6 w-1/3" : "h-16 w-full"} />
      ))}
    </div>
  );
}

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="rounded-xl border border-dashed border-white/10 px-5 py-8 text-center">
      <p className="text-sm font-medium text-fog-300">{title}</p>
      {children && <div className="mt-2 text-sm text-fog-500">{children}</div>}
    </div>
  );
}

export function SimTag({ className }: { className?: string }) {
  return (
    <span className={cx("inline-flex items-center rounded-md bg-white/[0.05] px-1.5 py-0.5 font-mono text-[10px] tracking-wide text-fog-400 ring-1 ring-inset ring-white/10", className)}>
      SYNTHETIC
    </span>
  );
}

export function TextLink({ to, children }: { to: string; children: ReactNode }) {
  return (
    <Link to={to} className="text-sm font-medium text-lilac-200 underline-offset-4 hover:underline">
      {children}
    </Link>
  );
}

export function timeAgo(iso: string) {
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 60) return `${Math.round(s)}s ago`;
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return new Date(iso).toLocaleDateString();
}

/** Full local date and time, e.g. "24 Oct 2026, 8:11 pm", for records people may refer back to. */
export function when(iso: string) {
  return new Date(iso).toLocaleString([], { day: "numeric", month: "short", year: "numeric", hour: "numeric", minute: "2-digit" });
}

export function clock(iso: string) {
  return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

export const AGENT_LABEL: Record<string, string> = {
  sentinel: "Sentinel",
  mobility: "Mobility",
  traffic: "AnalyseTraffic",
  grid: "Grid / Utility",
  coordinator: "Coordinator",
  validator: "Validator",
};
