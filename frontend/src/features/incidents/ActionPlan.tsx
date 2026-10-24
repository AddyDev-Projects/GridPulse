import { useState } from "react";
import { CaretDown, CheckCircle, Info, Question, Warning, XCircle } from "@phosphor-icons/react";
import { AGENT_LABEL, Badge, StatusBadge, cx, humanize } from "../../components/ui";
import { COLORS } from "../map/mapTheme";
import { FindingCard, describeAction, km, mins } from "./shared";
import type { Department, Finding, Incident, Plan, PlanAction, RouteLeg, ServiceRequirement } from "../../types/api";

/**
 * The operator's view of ONE plan version, built only from what the API returns for that plan:
 * why each department is involved (with its route legs in this version), the ordered actions with
 * expandable details, validation grouped by severity, and the open unknowns. Nothing is invented here:
 * a missing reason is shown as "Reason not provided — review required".
 */

const STATUS_TEXT: Record<Department["status"], string> = {
  required: "Required", recommended: "Recommended", conditional: "Conditional", not_justified: "Not justified",
};
const STATUS_TONE = { required: "accent", recommended: "info", conditional: "warn", not_justified: "neutral" } as const;
const BASIS_TEXT = { reported: "From the report", inference: "Inference from the report text", rule: "Operational rule" } as const;
const SERVICE_COLOR: Record<string, string> = { fire_rescue: COLORS.route, police: COLORS.routePolice, ambulance: COLORS.routeAlt };
const LEG_TEXT = { to_incident: "To the incident", to_hospital: "Incident to hospital" } as const;
const NO_REASON = "Reason not provided — review required.";
const LEGACY: Record<string, Department["status"]> = { optional: "conditional", not_needed: "not_justified" };

/** Departments recorded by older runs (without route legs) shown with today's status names. */
export function departmentsFromServices(services: ServiceRequirement[]): Department[] {
  const label = { fire_rescue: "Fire & rescue", police: "Police", ambulance: "Ambulance / medical" } as const;
  return services.map((s) => ({
    service: s.service, label: label[s.service], status: (LEGACY[s.status] ?? s.status) as Department["status"],
    basis: s.basis ?? "reported", rationale: s.rationale, evidence: s.evidence,
    unknowns: s.unknowns ?? (s.uncertainty ? [s.uncertainty] : []), expected_action: s.expected_action ?? null, routes: [],
  }));
}

function Leg({ leg }: { leg: RouteLeg }) {
  if (leg.status === "planned") {
    return (
      <li className="flex flex-wrap items-baseline gap-x-2 text-xs text-fog-300">
        <CheckCircle size={13} weight="fill" className="translate-y-0.5 text-ok" />
        <span className="text-fog-500">{LEG_TEXT[leg.leg]}:</span>
        <span>{leg.origin_label} → {leg.destination_label}</span>
        {leg.length_m != null && leg.travel_s != null && <span className="font-mono text-fog-400">{km(leg.length_m)}, {mins(leg.travel_s)} (estimate)</span>}
        {leg.action_seq != null && <span className="font-mono text-fog-500">action #{leg.action_seq}</span>}
      </li>
    );
  }
  return (
    <li className="flex items-start gap-2 text-xs text-warn">
      {leg.status === "withheld" ? <Info size={13} className="mt-0.5 shrink-0" /> : <Warning size={13} className="mt-0.5 shrink-0" />}
      <span><span className="text-fog-400">{LEG_TEXT[leg.leg]}:</span>{" "}
        {leg.status === "withheld" ? "Route withheld. " : "No valid road route could be calculated. "}{leg.reason ?? NO_REASON}</span>
    </li>
  );
}

export function DepartmentList({ departments }: { departments: Department[] }) {
  const [showOthers, setShowOthers] = useState(false);
  const involved = departments.filter((d) => d.status !== "not_justified");
  const others = departments.filter((d) => d.status === "not_justified");
  const order: Department["status"][] = ["required", "recommended", "conditional"];
  involved.sort((a, b) => order.indexOf(a.status) - order.indexOf(b.status));
  return (
    <section aria-labelledby="why-departments">
      <h3 id="why-departments" className="mb-2 text-sm font-medium text-fog-100">Why each department is involved</h3>
      {involved.length === 0 && (
        <p className="rounded-xl bg-white/[0.03] p-3 text-sm text-fog-400">
          No department is justified by the report yet. The plan asks for verification before anyone is sent.
        </p>
      )}
      <ul className="grid gap-2.5">
        {involved.map((d) => (
          <li key={d.service} className="rounded-xl border border-white/[0.08] bg-white/[0.02] p-3.5">
            <div className="flex flex-wrap items-center gap-2">
              <span aria-hidden className="h-2.5 w-2.5 rounded-full ring-1 ring-black/40" style={{ background: SERVICE_COLOR[d.service] }} />
              <span className="text-sm font-medium text-fog-100">{d.label}</span>
              <Badge tone={STATUS_TONE[d.status]}>{STATUS_TEXT[d.status]}</Badge>
              <span className="text-xs text-fog-500">{BASIS_TEXT[d.basis]}</span>
            </div>
            <p className="mt-1.5 text-[13px] leading-relaxed text-fog-300">{d.rationale?.trim() || NO_REASON}</p>
            {d.evidence.length > 0 && <p className="mt-1 text-xs text-fog-500">Evidence: {d.evidence.join("; ")}</p>}
            {d.expected_action && <p className="mt-1 text-xs text-fog-400">Expected action: {d.expected_action}</p>}
            {d.status !== "required" && <p className="mt-1 text-xs text-fog-500">Not dispatched: no route is planned for this department.</p>}
            {d.unknowns.length > 0 && <p className="mt-1 text-xs text-warn">Unknown: {d.unknowns.join("; ")}</p>}
            {d.routes.length > 0 && <ul className="mt-2 grid gap-1">{d.routes.map((l) => <Leg key={l.leg} leg={l} />)}</ul>}
          </li>
        ))}
      </ul>
      {others.length > 0 && (
        <div className="mt-2">
          <button type="button" onClick={() => setShowOthers((o) => !o)} aria-expanded={showOthers}
            className="inline-flex items-center gap-1 text-xs text-fog-400 hover:text-fog-100 focus-visible:outline-2 focus-visible:outline-lilac-300">
            <CaretDown size={12} className={cx("transition", showOthers && "rotate-180")} />
            Not involved: {others.map((d) => d.label).join(", ")}
          </button>
          {showOthers && (
            <ul className="mt-1.5 grid gap-1 text-xs text-fog-500">
              {others.map((d) => <li key={d.service}><span className="text-fog-300">{d.label}:</span> {d.rationale?.trim() || NO_REASON}</li>)}
            </ul>
          )}
        </div>
      )}
    </section>
  );
}

const DEPT_OF: Record<string, string> = { fire_rescue: "Fire & rescue", police: "Police", ambulance: "Ambulance / medical" };
const PURPOSE_DEPT: Record<string, string> = { emergency_access: "fire_rescue", police_response: "police", medical_transfer: "ambulance" };

function responsible(a: PlanAction): { label: string; color?: string } {
  if (a.action_type === "designate_response_route") {
    const svc = (a.params.service as string | undefined) ?? PURPOSE_DEPT[a.params.purpose as string];
    if (svc) return { label: DEPT_OF[svc] ?? humanize(svc), color: SERVICE_COLOR[svc] };
  }
  return { label: AGENT_LABEL[a.proposed_by] ?? humanize(a.proposed_by) };
}

function ActionItem({ a, findings }: { a: PlanAction; findings: Finding[] }) {
  const [open, setOpen] = useState(false);
  const who = responsible(a);
  const p = a.params;
  const isRoute = a.action_type === "designate_response_route";
  const worst = findings.find((f) => f.severity === "blocking") ? "blocking" : findings.find((f) => f.severity === "warning") ? "warning" : null;
  const id = `action-${a.seq}-details`;
  return (
    <li className={cx("rounded-xl border", worst === "blocking" ? "border-bad/40 bg-bad/[0.04]" : "border-white/[0.07] bg-white/[0.02]")}>
      <button type="button" onClick={() => setOpen((o) => !o)} aria-expanded={open} aria-controls={id}
        className="flex w-full flex-wrap items-center gap-2 p-3.5 text-left focus-visible:outline-2 focus-visible:outline-lilac-300">
        <span className="font-mono text-xs text-fog-500">#{a.seq}</span>
        <span className="text-sm font-medium text-fog-100">{a.label}</span>
        <span className="inline-flex items-center gap-1.5 rounded-full bg-white/[0.05] px-2 py-0.5 text-[11px] text-fog-300 ring-1 ring-inset ring-white/10">
          {who.color && <span aria-hidden className="h-2 w-2 rounded-full" style={{ background: who.color }} />}
          {who.label}
        </span>
        <Badge tone={a.impact === "high" ? "bad" : a.impact === "medium" ? "warn" : "neutral"}>{a.impact} impact</Badge>
        {worst && <Badge tone={worst === "blocking" ? "bad" : "warn"}>{worst === "blocking" ? "blocked" : "warning"}</Badge>}
        <CaretDown size={14} className={cx("ml-auto text-fog-500 transition", open && "rotate-180")} />
        <span className="basis-full text-[13px] text-fog-300">{describeAction(a, a.target_name)}</span>
      </button>
      {open && (
        <div id={id} className="grid gap-2 border-t border-white/[0.06] px-3.5 py-3 text-xs text-fog-400">
          {isRoute ? (
            <dl className="grid grid-cols-1 gap-x-4 gap-y-1 sm:grid-cols-2">
              <div><dt className="text-fog-500">From</dt><dd className="text-fog-200">{String(p.origin_label ?? p.origin)}</dd></div>
              <div><dt className="text-fog-500">To</dt><dd className="text-fog-200">{String(p.destination_label ?? p.destination)}</dd></div>
              <div><dt className="text-fog-500">Distance and time</dt>
                <dd className="font-mono text-fog-200">{km(p.length_m as number)}, {mins(p.travel_s as number)} (free-flow estimate)</dd></div>
              <div><dt className="text-fog-500">Road segments</dt><dd className="font-mono text-fog-200">{(p.edge_path as string[]).length}</dd></div>
              {!a.geometry?.length && <div className="sm:col-span-2 text-warn">Route geometry is missing; it cannot be drawn on the map.</div>}
            </dl>
          ) : (
            <p><span className="text-fog-500">Target:</span> <span className="text-fog-200">{a.target_name ?? a.target_id}</span></p>
          )}
          <p><span className="text-fog-500">Why:</span> {a.rationale || NO_REASON}</p>
          <p><span className="text-fog-500">Proposed by:</span> {AGENT_LABEL[a.proposed_by] ?? a.proposed_by}</p>
          {findings.length > 0 && <ul className="grid gap-2">{findings.map((f) => <FindingCard key={f.id} f={f} />)}</ul>}
        </div>
      )}
    </li>
  );
}

function FindingGroup({ title, tone, findings, icon }: { title: string; tone: string; findings: Finding[]; icon: React.ReactNode }) {
  if (findings.length === 0) return null;
  return (
    <div>
      <h4 className={cx("mb-1.5 flex items-center gap-1.5 text-xs font-medium", tone)}>{icon} {title} ({findings.length})</h4>
      <ul className="grid gap-2">{findings.map((f) => <FindingCard key={f.id} f={f} />)}</ul>
    </div>
  );
}

export function ActionPlan({ plan, incident, departments, unknowns = [] }: {
  plan: Plan; incident?: Incident; departments?: Department[]; unknowns?: string[];
}) {
  const v = plan.latest_validation;
  const findings = v?.findings ?? [];
  const bySeq = (seq: number) => findings.filter((f) => f.action_seq === seq);
  const general = findings.filter((f) => f.action_seq == null);
  const depts = plan.departments?.length ? plan.departments : departments ?? [];
  const open = Array.from(new Set([...depts.flatMap((d) => (d.status === "not_justified" ? [] : d.unknowns)), ...unknowns])).slice(0, 8);
  return (
    <div className="grid gap-5" data-testid={`action-plan-v${plan.version}`}>
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm text-fog-100">Plan v{plan.version}</span>
        <StatusBadge status={plan.status} />
        {incident?.reference && <span className="font-mono text-xs text-lilac-200">{incident.reference}</span>}
        {incident?.urgency && <Badge tone={incident.urgency === "critical" ? "bad" : incident.urgency === "high" ? "warn" : "neutral"}>{incident.urgency} urgency</Badge>}
        <span className={cx("ml-auto inline-flex items-center gap-1.5 text-xs", !v ? "text-fog-500" : v.passed ? "text-ok" : "text-bad")}>
          {!v ? "Not validated" : v.passed ? <><CheckCircle size={14} weight="fill" /> Passed validation</> : <><XCircle size={14} weight="fill" /> Blocked</>}
        </span>
      </div>
      {plan.summary && <p className="text-sm text-fog-300">{plan.summary}</p>}

      {depts.length > 0 ? <DepartmentList departments={depts} /> : (
        <p className="rounded-xl bg-warn/[0.06] p-3 text-sm text-warn">
          This plan was made before department assessment was recorded. Reason not provided — review required.
        </p>
      )}

      <section aria-label="Planned actions">
        <h3 className="mb-2 text-sm font-medium text-fog-100">Actions, in order</h3>
        {plan.actions.length === 0 ? <p className="text-sm text-fog-500">This plan has no actions.</p> : (
          <ul className="grid gap-2">{plan.actions.map((a) => <ActionItem key={`${plan.id}-${a.seq}`} a={a} findings={bySeq(a.seq)} />)}</ul>
        )}
      </section>

      <section aria-label="Validation" className="grid gap-3">
        <h3 className="text-sm font-medium text-fog-100">Validation</h3>
        {!v ? <p className="text-sm text-fog-500">Not validated yet.</p> : (
          <>
            <FindingGroup title="Hard failures (block approval)" tone="text-bad" icon={<XCircle size={14} weight="fill" />}
              findings={general.filter((f) => f.severity === "blocking")} />
            <FindingGroup title="Warnings" tone="text-warn" icon={<Warning size={14} weight="fill" />}
              findings={general.filter((f) => f.severity === "warning")} />
            <FindingGroup title="Notes" tone="text-fog-400" icon={<Info size={14} />}
              findings={general.filter((f) => f.severity === "info")} />
            {general.length === 0 && <p className="text-sm text-fog-500">No plan-wide findings; action-specific findings are under each action.</p>}
          </>
        )}
      </section>

      {open.length > 0 && (
        <section aria-label="Unknowns">
          <h3 className="mb-1.5 flex items-center gap-1.5 text-sm font-medium text-fog-100"><Question size={15} /> Still unknown</h3>
          <ul className="grid list-disc gap-0.5 pl-5 text-xs text-fog-400">{open.map((u) => <li key={u}>{u}</li>)}</ul>
        </section>
      )}
    </div>
  );
}
