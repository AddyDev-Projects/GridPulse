import { useState } from "react";
import { Link } from "react-router";
import { ShieldCheck, ShieldWarning, Lightning, ArrowsClockwise, CheckCircle, Prohibit } from "@phosphor-icons/react";
import { useIncidentContext } from "./IncidentLayout";
import { ActionRow, FindingCard } from "./shared";
import { useDecision, useExecute, useMe, useRevalidate } from "../../hooks/queries";
import { Badge, Button, Empty, InlineError, Panel, StatusBadge, clock, cx } from "../../components/ui";
import type { Plan } from "../../types/api";

function PlanIdentity({ plan }: { plan: Plan }) {
  const v = plan.latest_validation;
  return (
    <dl className="grid grid-cols-2 gap-4 text-sm sm:grid-cols-4">
      <div>
        <dt className="text-xs text-fog-500">Plan version</dt>
        <dd className="font-display text-xl text-fog-100">v{plan.version}</dd>
      </div>
      <div>
        <dt className="text-xs text-fog-500">Content hash</dt>
        <dd className="font-mono text-xs text-fog-300" title={plan.content_hash}>{plan.content_hash.slice(0, 16)}…</dd>
      </div>
      <div className="col-span-2">
        <dt className="text-xs text-fog-500">City state</dt>
        {/* The version numbers stay internal; the operator sees what they mean. */}
        <dd className={cx(!v ? "text-fog-400" : v.state_version !== plan.current_state_version && plan.status !== "EXECUTED" ? "text-warn" : "text-ok")}>
          {!v ? "Not validated yet" : plan.status === "EXECUTED" ? "Applied to the simulation"
            : v.state_version !== plan.current_state_version ? "Changed since this plan was validated" : "Unchanged since validation"}
        </dd>
      </div>
    </dl>
  );
}

function ExecutionResult({ plan }: { plan: Plan }) {
  const ex = plan.execution!;
  return (
    <Panel title={<span className="flex items-center gap-2"><CheckCircle size={18} weight="fill" className="text-ok" /> Executed in the simulation</span>}
      action={<Badge tone="ok">simulated city state updated</Badge>}>
      <p className="text-sm text-fog-400">
        {ex.executed_by} executed plan v{plan.version} at {clock(ex.executed_at)}. {ex.notes}
      </p>
      <ul className="mt-4 grid gap-2">
        {ex.results.map((r) => (
          <li key={r.seq} className="rounded-xl border border-white/[0.07] bg-white/[0.02] p-3 text-sm">
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-mono text-xs text-fog-500">#{r.seq}</span>
              <span className="text-fog-100">{r.action_type.replace(/_/g, " ")}</span>
              <Badge tone={r.status === "applied" ? "ok" : "neutral"}>{r.status}</Badge>
            </div>
            {r.changes.length > 0 && (
              <ul className="mt-1.5 grid gap-0.5 font-mono text-xs text-fog-400">
                {r.changes.map((c) => (
                  <li key={c.id + c.after}>{c.name ?? c.id}: {c.before ? `${c.before} → ` : ""}{c.after}</li>
                ))}
              </ul>
            )}
            {r.note && <p className="mt-1 text-xs text-fog-500">{r.note}</p>}
          </li>
        ))}
      </ul>
      <Link to="/ops/city" className="mt-4 inline-block text-sm text-lilac-200 underline-offset-4 hover:underline">See the updated city graph</Link>
    </Panel>
  );
}

export default function ApprovalTab() {
  const { wf } = useIncidentContext();
  const plan = wf.plans[wf.plans.length - 1];
  const me = useMe();
  const [note, setNote] = useState("");
  const [confirm, setConfirm] = useState<null | "approve" | "reject" | "execute">(null);
  const approve = useDecision("approve");
  const reject = useDecision("reject");
  const execute = useExecute();
  const revalidate = useRevalidate();

  if (!plan) return <Empty title={wf.running ? "Waiting for a validated plan" : "No plan to review"} />;

  const opValid = Boolean(me.data);
  const decisionBody = { plan_version: plan.version, content_hash: plan.content_hash, note: note.trim() || undefined };
  const lastError = approve.error ?? reject.error ?? execute.error ?? revalidate.error;
  const activeApproval = plan.approvals.find((a) => a.decision === "approved" && a.status === "active");
  const blocking = plan.latest_validation?.findings.filter((f) => f.severity !== "info") ?? [];
  const escalated = plan.status === "ESCALATED" || plan.status === "INVALID";

  const act = (kind: "approve" | "reject" | "execute") => {
    const done = { onSettled: () => setConfirm(null) };
    if (kind === "execute") execute.mutate(plan.id, done);
    else (kind === "approve" ? approve : reject).mutate({ planId: plan.id, body: decisionBody }, done);
  };

  return (
    <div className="grid grid-cols-1 gap-6 lg:grid-cols-12">
      <div className="grid min-w-0 grid-cols-1 content-start gap-6 lg:col-span-7">
        {plan.execution && <ExecutionResult plan={plan} />}

        {escalated && (
          <Panel title={<span className="flex items-center gap-2"><ShieldWarning size={18} className="text-bad" /> Escalated to a human coordinator</span>}>
            <p className="text-sm text-fog-400">
              GridPulse could not produce a plan that passes validation, so nothing can be approved or executed and the
              simulated city is unchanged.
            </p>
            <ul className="mt-4 grid gap-2">
              {plan.latest_validation?.findings.filter((f) => f.severity === "blocking").map((f) => <FindingCard key={f.id} f={f} />)}
            </ul>
          </Panel>
        )}

        <Panel title="Plan under review" action={<StatusBadge status={plan.status} />}>
          <PlanIdentity plan={plan} />
          <p className="mt-4 text-sm text-fog-400">{plan.summary}</p>
          <ul className="mt-4 grid gap-2">
            {plan.actions.map((a) => <ActionRow key={a.seq} a={a} />)}
          </ul>
        </Panel>

        {blocking.length > 0 && !escalated && (
          <Panel title="Risks and warnings to acknowledge">
            <ul className="grid gap-2">{blocking.map((f) => <FindingCard key={f.id} f={f} />)}</ul>
          </Panel>
        )}
      </div>

      <div className="lg:col-span-5 min-w-0">
        <div className="panel sticky top-20 grid gap-5 p-5">
          <div className="flex items-center gap-2">
            <ShieldCheck size={20} className="text-lilac-200" />
            <h2 className="font-display text-[15px] font-semibold text-fog-100">Operator decision</h2>
          </div>

          {plan.approvals.length > 0 && (
            <ul className="grid gap-2 text-sm">
              {plan.approvals.map((a) => (
                <li key={a.id} className="rounded-xl bg-white/[0.03] p-3">
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge tone={a.decision === "approved" ? "ok" : "neutral"}>{a.decision}</Badge>
                    <Badge tone={a.status === "invalidated" ? "warn" : "neutral"}>{a.status}</Badge>
                    <span className="text-xs text-fog-500">by {a.operator} at {clock(a.created_at)}</span>
                  </div>
                  <p className="mt-1 text-xs text-fog-500">Bound to plan v{a.plan_version} (hash {a.plan_content_hash.slice(0, 10)}…) and the city state at the time</p>
                  {a.invalidated_reason && <p className="mt-1 text-xs text-warn">{a.invalidated_reason}</p>}
                  {a.note && <p className="mt-1 text-xs text-fog-400">“{a.note}”</p>}
                </li>
              ))}
            </ul>
          )}

          {plan.status === "REJECTED" && (
            <p className="flex items-start gap-2 rounded-xl bg-white/[0.03] p-3 text-sm text-fog-300">
              <Prohibit size={18} className="mt-0.5 shrink-0" /> Plan rejected. No simulated actions were applied.
            </p>
          )}

          {!plan.execution && plan.status !== "REJECTED" && !escalated && (
            <>
              <p className="text-sm text-fog-300">Deciding as <span className="text-fog-100">{me.data?.username}</span>
                {me.data?.role === "admin" ? " (admin)" : ""}. Your account is recorded with the decision.</p>

              {plan.status === "AWAITING_APPROVAL" && (
                <div className="grid gap-2">
                  <label htmlFor="note" className="text-sm text-fog-300">Note <span className="text-fog-500">(optional)</span></label>
                  <textarea id="note" rows={2} value={note} onChange={(e) => setNote(e.target.value)} maxLength={500}
                    className="rounded-xl border border-white/10 bg-ink-900 px-4 py-2.5 text-sm text-fog-100 focus:border-lilac-300/60 focus:outline-none" />
                </div>
              )}

              {!plan.approvable && plan.status === "AWAITING_APPROVAL" && (
                <ul className="grid gap-1 text-xs text-warn">{plan.approval_blockers.map((b) => <li key={b}>{b}</li>)}</ul>
              )}

              {plan.status === "STALE" && (
                <div className="grid gap-2 rounded-xl bg-warn/[0.06] p-3 ring-1 ring-inset ring-warn/20">
                  <p className="text-sm text-warn">The city state has changed since this plan was generated. Revalidate the plan before approving or executing it.</p>
                  <Button variant="ghost" disabled={!opValid} loading={revalidate.isPending} onClick={() => revalidate.mutate(plan.id)}>
                    <ArrowsClockwise size={15} /> Revalidate against current state
                  </Button>
                </div>
              )}

              {confirm ? (
                <div role="alertdialog" aria-labelledby="confirm-title" className="grid gap-3 rounded-xl border border-white/12 bg-white/[0.04] p-4">
                  <p id="confirm-title" className="text-sm text-fog-100">
                    {confirm === "approve" && `Approve plan v${plan.version} (${plan.content_hash.slice(0, 10)}…)? This authorizes ${plan.actions.length} simulated actions against the current city state.`}
                    {confirm === "reject" && `Reject plan v${plan.version}? No simulated actions will be applied.`}
                    {confirm === "execute" && `Execute approved plan v${plan.version} in the simulation? The backend revalidates it against the current city state first.`}
                  </p>
                  <div className="flex gap-2">
                    <Button variant={confirm === "reject" ? "danger" : confirm === "execute" ? "ok" : "primary"} autoFocus
                      loading={approve.isPending || reject.isPending || execute.isPending} onClick={() => act(confirm)}>
                      Confirm {confirm}
                    </Button>
                    <Button variant="ghost" onClick={() => setConfirm(null)}>Cancel</Button>
                  </div>
                </div>
              ) : (
                <div className="flex flex-wrap gap-2">
                  {plan.status === "AWAITING_APPROVAL" && (
                    <>
                      <Button disabled={!plan.approvable || !opValid} onClick={() => setConfirm("approve")}>
                        <ShieldCheck size={16} /> Approve v{plan.version}
                      </Button>
                      <Button variant="danger" disabled={!opValid} onClick={() => setConfirm("reject")}>Reject</Button>
                    </>
                  )}
                  {plan.status === "APPROVED" && activeApproval && (
                    <>
                      <Button variant="ok" disabled={!opValid} onClick={() => setConfirm("execute")}>
                        <Lightning size={16} weight="fill" /> Execute in simulation
                      </Button>
                      <Button variant="danger" disabled={!opValid} onClick={() => setConfirm("reject")}>Withdraw and reject</Button>
                    </>
                  )}
                </div>
              )}
            </>
          )}

          <InlineError error={lastError} />
          <p className="text-xs leading-relaxed text-fog-500">
            The server enforces every rule here: only signed-in operators can decide, approval binds this exact version,
            hash and city state, execution revalidates first, and duplicate requests are refused. Execution changes only
            the simulation; nothing is dispatched in the real world.
          </p>
        </div>
      </div>
    </div>
  );
}
