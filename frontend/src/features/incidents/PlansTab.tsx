import { useState } from "react";
import { ArrowRight, CheckCircle, XCircle, GitBranch } from "@phosphor-icons/react";
import { useIncidentContext } from "./IncidentLayout";
import { ActionRow, FindingCard } from "./shared";
import { useGraph } from "../../hooks/queries";
import { AGENT_LABEL, Badge, Empty, Panel, Skeleton, StatusBadge, TextLink, cx, humanize } from "../../components/ui";
import { CityGraph, GraphLegend, type RouteOverlay } from "../simulation/CityGraph";
import type { Plan, Revision } from "../../types/api";

const routeOf = (p: Plan) => p.actions.find((a) => a.action_type === "designate_evacuation_route");

function PlanColumn({ plan, title, revision, side }: { plan: Plan; title: string; revision?: Revision | null; side: "before" | "after" }) {
  const v = plan.latest_validation;
  const blockingSeqs = new Set(v?.findings.filter((f) => f.severity === "blocking").map((f) => f.action_seq));
  const added = new Set(revision?.diff.added.map((a) => `${a.action_type}:${a.target_id}`));
  const changed = new Set(revision?.diff.changed.map((c) => `${c.after.action_type}:${c.after.target_id}`));
  const findings = [...(v?.findings ?? [])].sort((a, b) => ["blocking", "warning", "info"].indexOf(a.severity) - ["blocking", "warning", "info"].indexOf(b.severity));
  return (
    <Panel
      title={
        <span className="flex items-center gap-2">
          {title} <span className="font-mono text-xs font-normal text-fog-500">v{plan.version}</span>
        </span>
      }
      action={<StatusBadge status={plan.status} />}
    >
      <div className="grid gap-5">
        <div
          className={cx(
            "flex items-center gap-2 rounded-xl px-3.5 py-2.5 text-sm",
            v?.passed ? "bg-ok/[0.07] text-ok ring-1 ring-inset ring-ok/20" : "bg-bad/[0.07] text-bad ring-1 ring-inset ring-bad/25",
          )}
        >
          {v?.passed ? <CheckCircle size={17} weight="fill" /> : <XCircle size={17} weight="fill" />}
          {v ? (v.passed ? "Passed validation" : `Blocked: ${v.blocking_count} blocking finding${v.blocking_count === 1 ? "" : "s"}`) : "Not validated"}
          {v && <span className="ml-auto text-xs text-fog-500">{v.warning_count} warnings · state v{v.state_version}</span>}
        </div>
        <div>
          <h3 className="mb-2 text-xs text-fog-500">Proposed actions</h3>
          <ul className="grid gap-2">
            {plan.actions.map((a) => {
              const key = `${a.action_type}:${a.target_id}`;
              const hl = side === "before" && blockingSeqs.has(a.seq) ? "conflict" : side === "after" && added.has(key) ? "added" : side === "after" && changed.has(key) ? "changed" : undefined;
              return <ActionRow key={a.seq} a={a} highlight={hl} />;
            })}
          </ul>
        </div>
        <div>
          <h3 className="mb-2 text-xs text-fog-500">Validator findings</h3>
          {findings.length === 0 ? <p className="text-sm text-fog-500">No findings.</p> : <ul className="grid gap-2">{findings.map((f) => <FindingCard key={f.id} f={f} />)}</ul>}
        </div>
      </div>
    </Panel>
  );
}

function RevisionCard({ r }: { r: Revision }) {
  const before = r.diff.route_before, after = r.diff.route_after;
  return (
    <Panel title={<span className="flex items-center gap-2"><GitBranch size={16} className="text-lilac-200" /> How revision {r.revision_number} resolved it</span>}
      action={<Badge tone="accent">{r.strategy.split("+").map(humanize).join(" + ")}</Badge>}>
      <div className="grid gap-5 lg:grid-cols-2">
        <div className="grid content-start gap-3">
          <p className="text-[14px] leading-relaxed text-fog-300">{r.rationale}</p>
          <p className="text-xs text-fog-500">Triggered by: {r.triggered_by.map((t) => t.title).join("; ")}</p>
          {(before || after) && (
            <div className="grid gap-2 rounded-xl bg-white/[0.025] p-3.5 text-sm">
              <div className="flex flex-wrap items-center gap-2">
                <span className="w-14 text-xs text-fog-500">Before</span>
                <span className="font-mono text-xs text-bad">{before ? before.node_path.join(" → ") : "no route"}</span>
                {before && <span className="text-xs text-fog-500">{Math.round(before.length_m)} m</span>}
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <span className="w-14 text-xs text-fog-500">After</span>
                <span className="font-mono text-xs text-ok">{after ? after.node_path.join(" → ") : "route withheld"}</span>
                {after && <span className="text-xs text-fog-500">{Math.round(after.length_m)} m{before ? ` (${after.length_m - before.length_m >= 0 ? "+" : ""}${Math.round(after.length_m - before.length_m)} m)` : ""}</span>}
              </div>
            </div>
          )}
          <ul className="flex flex-wrap gap-2 text-xs">
            {r.diff.added.map((a) => <Badge key={`a${a.seq}`} tone="ok">+ {humanize(a.action_type)} {a.target_id}</Badge>)}
            {r.diff.removed.map((a) => <Badge key={`r${a.seq}`} tone="bad">− {humanize(a.action_type)} {a.target_id}</Badge>)}
            {r.diff.changed.map((c) => <Badge key={`c${c.after.seq}`} tone="accent">~ {humanize(c.after.action_type)}</Badge>)}
          </ul>
        </div>
        <div>
          <h3 className="mb-2 text-xs text-fog-500">Alternatives the agents evaluated</h3>
          {r.alternatives.length === 0 ? (
            <p className="text-sm text-fog-500">Single remediation path for this finding type.</p>
          ) : (
            <ul className="grid gap-2">
              {r.alternatives.map((alt) => (
                <li key={alt.strategy} className={cx("rounded-xl border p-3.5", alt.selected ? "border-ok/35 bg-ok/[0.04]" : "border-white/[0.07]")}>
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-sm text-fog-100">{humanize(alt.strategy)}</span>
                    <Badge>{AGENT_LABEL[alt.agent] ?? alt.agent}</Badge>
                    {alt.selected ? <Badge tone="ok">selected</Badge> : <Badge tone={alt.viable ? "neutral" : "bad"}>{alt.viable ? "viable, not selected" : "not viable"}</Badge>}
                  </div>
                  <p className="mt-1.5 text-xs leading-relaxed text-fog-400">{alt.detail}</p>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </Panel>
  );
}

function CoordinationComparison({ first, final }: { first: Plan; final: Plan }) {
  const r1 = routeOf(first), rf = routeOf(final);
  const conflict = first.latest_validation?.findings.find((f) => f.severity === "blocking");
  const rows: [string, string, string][] = [
    ["Blocking safety findings", String(first.latest_validation?.blocking_count ?? 0), String(final.latest_validation?.blocking_count ?? 0)],
    ["Evacuation route", r1 ? (r1.params.node_path as string[]).join(" → ") : "none", rf ? (rf.params.node_path as string[]).join(" → ") : "none"],
    ["Route length", r1 ? `${Math.round(r1.params.length_m as number)} m` : "n/a", rf ? `${Math.round(rf.params.length_m as number)} m` : "n/a"],
    ["Outcome", "Blocked by the validator, never executed", humanize(final.status)],
  ];
  return (
    <Panel title="Why coordination matters">
      <p className="mb-4 max-w-[80ch] text-sm text-fog-400">
        Plan v1 is what the agents produce independently, with no cross-checking. It is never executed; it is shown here
        as the uncoordinated baseline. {conflict ? `The validator found: ${conflict.title.toLowerCase()}.` : ""}
      </p>
      <div className="scrollbar-thin overflow-x-auto">
        <table className="w-full min-w-[520px] text-left text-sm">
          <thead className="text-xs text-fog-500">
            <tr>
              <th className="py-2 font-normal" />
              <th className="py-2 font-normal">Uncoordinated (v{first.version})</th>
              <th className="py-2 font-normal">GridPulse (v{final.version})</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(([k, a, b]) => (
              <tr key={k} className="border-t border-white/[0.05]">
                <td className="py-2.5 pr-4 text-fog-500">{k}</td>
                <td className="py-2.5 pr-4 font-mono text-xs text-bad">{a}</td>
                <td className="py-2.5 font-mono text-xs text-ok">{b}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Panel>
  );
}

export default function PlansTab() {
  const { wf } = useIncidentContext();
  const graph = useGraph();
  const [revIdx, setRevIdx] = useState<number | null>(null);
  if (wf.plans.length === 0) return <Empty title={wf.running ? "The coordinator is assembling the first plan" : "No plans yet"} />;

  const revisions = wf.revisions;
  const idx = revIdx ?? revisions.length - 1;
  const rev = revisions[idx];
  const before = rev ? wf.plans.find((p) => p.id === rev.from_plan_id)! : null;
  const after = rev ? wf.plans.find((p) => p.id === rev.to_plan_id)! : wf.plans[wf.plans.length - 1];
  const first = wf.plans[0];
  const final = wf.plans[wf.plans.length - 1];

  const overlays: RouteOverlay[] = [];
  const conflictPlan = before ?? (after.latest_validation?.passed ? null : after);
  const cr = conflictPlan && routeOf(conflictPlan);
  if (cr && conflictPlan) overlays.push({ node_path: cr.params.node_path as string[], label: `v${conflictPlan.version}`, tone: "conflict" });
  const ar = routeOf(after);
  if (ar && after !== conflictPlan) overlays.push({ node_path: ar.params.node_path as string[], label: `v${after.version}`, tone: after.latest_validation?.passed ? "revised" : "proposed" });
  const chainFinding = (conflictPlan ?? after).latest_validation?.findings.find((f) => Array.isArray(f.evidence?.dependency_chain));
  const consequences = after.latest_validation?.findings.flatMap((f) => (f.evidence?.consequences as { id: string }[] | undefined) ?? []) ?? [];

  return (
    <div className="grid grid-cols-1 gap-6">
      {revisions.length > 1 && (
        <div role="tablist" aria-label="Revision" className="flex gap-2">
          {revisions.map((r, i) => (
            <button key={r.id} role="tab" aria-selected={i === idx} onClick={() => setRevIdx(i)}
              className={cx("rounded-full px-3 py-1.5 text-xs", i === idx ? "bg-white/10 text-fog-100" : "text-fog-400 hover:text-fog-100")}>
              Revision {r.revision_number}
            </button>
          ))}
        </div>
      )}

      <Panel title="Plan on the city graph" action={<TextLink to="/ops/city">Open city graph</TextLink>}>
        {graph.data ? (
          <CityGraph graph={graph.data} routes={overlays} chain={(chainFinding?.evidence?.dependency_chain as string[]) ?? []} projectedOff={consequences.map((c) => c.id)} />
        ) : (
          <Skeleton className="h-72 w-full" />
        )}
        <div className="mt-4 grid gap-3">
          <GraphLegend />
          <p className="text-xs text-fog-500">
            Shows current city state. Red dashed route: blocked plan. Green route: the validated revision. Circled nodes: the
            dependency chain the validator traced.
          </p>
        </div>
      </Panel>

      {rev && before ? (
        <>
          <RevisionCard r={rev} />
          <div className="grid grid-cols-1 gap-6 xl:grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)]">
            <PlanColumn plan={before} title="Before" side="before" />
            <div className="hidden items-center xl:flex" aria-hidden>
              <ArrowRight size={22} className="text-fog-500" />
            </div>
            <PlanColumn plan={after} title="After" side="after" revision={rev} />
          </div>
        </>
      ) : (
        <PlanColumn plan={after} title="Plan" side="before" />
      )}

      {wf.plans.length > 1 && <CoordinationComparison first={first} final={final} />}
    </div>
  );
}
