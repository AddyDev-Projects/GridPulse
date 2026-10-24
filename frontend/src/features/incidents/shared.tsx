import type { Finding, PlanAction, Workflow } from "../../types/api";
import { AGENT_LABEL, Badge, SeverityIcon, cx } from "../../components/ui";
import { CheckCircle, Circle, XCircle } from "@phosphor-icons/react";

export const STAGES: { status: string; label: string }[] = [
  { status: "REPORTED", label: "Reported" },
  { status: "TRIAGED", label: "Triaged" },
  { status: "EVIDENCE_GATHERED", label: "Evidence" },
  { status: "PLANS_PROPOSED", label: "Proposed" },
  { status: "VALIDATING", label: "Validated" },
  { status: "REVISION_REQUIRED", label: "Revised" },
  { status: "VALIDATING_AGAIN", label: "Revalidated" },
  { status: "AWAITING_APPROVAL", label: "Approval" },
  { status: "APPROVED", label: "Approved" },
  { status: "EXECUTED", label: "Executed" },
];
const TERMINAL_BAD = new Set(["REJECTED", "ESCALATED", "NO_SAFE_PLAN", "FAILED", "DISMISSED"]);

export function WorkflowStepper({ wf }: { wf: Workflow }) {
  const visited = new Set(wf.transitions.map((t) => t.to_status));
  const current = wf.incident.status;
  const terminalBad = TERMINAL_BAD.has(current);
  const lastIdx = Math.max(...STAGES.map((s, i) => (visited.has(s.status as never) ? i : -1)));
  return (
    <ol className="scrollbar-thin flex items-center gap-1 overflow-x-auto pb-1" aria-label="Workflow progress">
      {STAGES.map((s, i) => {
        const done = visited.has(s.status as never);
        const isCurrent = s.status === current;
        const skipped = !done && i < lastIdx;
        return (
          <li key={s.status} className="flex shrink-0 items-center gap-1">
            <span
              aria-current={isCurrent ? "step" : undefined}
              className={cx(
                "inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs",
                isCurrent && !terminalBad && "bg-lilac-300/12 text-lilac-200 ring-1 ring-inset ring-lilac-300/30",
                isCurrent && wf.running && "live-ring",
                !isCurrent && done && "text-fog-300",
                skipped && "text-fog-500 line-through decoration-white/20",
                !done && !skipped && "text-fog-500",
              )}
            >
              {done ? <CheckCircle size={13} weight="fill" className={s.status === "REVISION_REQUIRED" ? "text-warn" : "text-ok"} /> : <Circle size={13} />}
              {s.label}
            </span>
            {i < STAGES.length - 1 && <span className="h-px w-3 bg-white/10" aria-hidden />}
          </li>
        );
      })}
      {terminalBad && (
        <li className="ml-1 inline-flex shrink-0 items-center gap-1.5 rounded-full bg-bad/10 px-2.5 py-1 text-xs text-bad ring-1 ring-inset ring-bad/30">
          <XCircle size={13} weight="fill" /> {current.replace(/_/g, " ").toLowerCase()}
        </li>
      )}
    </ol>
  );
}

const IMPACT_TONE = { high: "bad", medium: "warn", low: "neutral" } as const;

export function SourceBadge({ source }: { source: string }) {
  if (source === "demo_scenario") return <Badge tone="warn">simulated demo</Badge>;
  if (source === "legacy") return <Badge>legacy</Badge>;
  return <Badge tone="info">citizen</Badge>;
}

export const km = (m: number) => `${(m / 1000).toFixed(2)} km`;
export const mins = (s: number) => `~${Math.max(1, Math.round(s / 60))} min`;

export function describeAction(a: { action_type: string; params: Record<string, unknown>; target_id: string }, targetName?: string | null) {
  const p = a.params;
  switch (a.action_type) {
    case "designate_response_route":
      return `${p.name}: ${km(p.length_m as number)}, ${mins(p.travel_s as number)} via ${(p.edge_path as string[]).length} road segments, to ${p.destination_label}`;
    case "close_roads":
      return `Close ${(p.segment_ids as string[]).length} road segment(s) within ${Math.round((p.radius_m as number) ?? 0)} m of ${targetName ?? a.target_id}`;
    case "set_roads_caution":
      return `Caution on ${(p.segment_ids as string[]).length} road segment(s) around ${targetName ?? a.target_id}`;
    case "transfer_signal_power":
      return `Move ${a.target_id} from ${p.from_feed} to ${p.to_feed}`;
    case "issue_public_advisory":
      return `Advisory: "${p.message}"`;
    case "dispatch_field_verification":
      return `Field check: ${p.question}`;
    default:
      return `${targetName ?? a.target_id} (${a.target_id})`;
  }
}

export function ActionRow({ a, highlight }: { a: PlanAction; highlight?: "added" | "changed" | "conflict" }) {
  return (
    <li
      className={cx(
        "rounded-xl border p-3.5",
        highlight === "conflict" ? "border-bad/40 bg-bad/[0.05]" : highlight ? "border-ok/35 bg-ok/[0.04]" : "border-white/[0.07] bg-white/[0.02]",
      )}
    >
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-mono text-xs text-fog-500">#{a.seq}</span>
        <span className="text-sm font-medium text-fog-100">{a.label}</span>
        <Badge tone={IMPACT_TONE[a.impact]}>{a.impact} impact</Badge>
        <Badge>{AGENT_LABEL[a.proposed_by] ?? a.proposed_by}</Badge>
        {highlight === "added" && <Badge tone="ok">added</Badge>}
        {highlight === "changed" && <Badge tone="ok">changed</Badge>}
      </div>
      <p className="mt-1.5 text-[13.5px] text-fog-300">{describeAction(a, a.target_name)}</p>
      <p className="mt-1 text-xs leading-relaxed text-fog-500">{a.rationale}</p>
    </li>
  );
}

const RULE_NAME: Record<string, string> = {
  R1_ROAD_SAFETY: "R1 Road safety",
  R2_INFRA_DEPENDENCY: "R2 Dependency",
  R3_ACTION_VALIDITY: "R3 Allowlist",
  R4_APPROVAL_REQUIRED: "R4 Approval",
  R5_STALE_STATE: "R5 Stale state",
  R6_EVIDENCE: "R6 Evidence",
  R7_UNRESOLVED_CONFLICT: "R7 Unresolved",
};

export function FindingCard({ f }: { f: Finding }) {
  const chain = f.evidence?.dependency_chain as string[] | undefined;
  const agents = f.evidence?.involved_agents as string[] | undefined;
  return (
    <li
      className={cx(
        "rounded-xl border p-4",
        f.severity === "blocking" ? "border-bad/35 bg-bad/[0.05]" : f.severity === "warning" ? "border-warn/25 bg-warn/[0.04]" : "border-white/[0.07] bg-white/[0.02]",
      )}
    >
      <div className="flex items-start gap-3">
        <SeverityIcon severity={f.severity} />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-mono text-[11px] text-fog-500">{RULE_NAME[f.rule_id] ?? f.rule_id}</span>
            {f.action_seq != null && <span className="font-mono text-[11px] text-fog-500">action #{f.action_seq}</span>}
            {(agents ?? (f.agent ? [f.agent] : [])).map((a) => (
              <Badge key={a}>{AGENT_LABEL[a] ?? a}</Badge>
            ))}
          </div>
          <p className="mt-1 text-sm font-medium text-fog-100">{f.title}</p>
          <p className="mt-1 text-[13px] leading-relaxed text-fog-400">{f.message}</p>
          {chain && (
            <p className="mt-2 flex flex-wrap items-center gap-1.5 font-mono text-xs">
              {chain.map((c, i) => (
                <span key={c} className="flex items-center gap-1.5">
                  <span className="rounded-md bg-bad/10 px-1.5 py-0.5 text-bad">{c}</span>
                  {i < chain.length - 1 && <span className="text-fog-500">→</span>}
                </span>
              ))}
            </p>
          )}
          {f.remediation && <p className="mt-2 text-xs text-fog-500">Remediation: {f.remediation}</p>}
        </div>
      </div>
    </li>
  );
}
