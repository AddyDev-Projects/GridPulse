import { useState } from "react";
import { NavLink, Outlet, useLocation, useOutletContext, useParams, Link } from "react-router";
import { ArrowLeft, CheckCircle, CircleNotch, FlagCheckered, Play, Prohibit } from "@phosphor-icons/react";
import { useIncident, useIncidentAction, useResolve, useWorkflow } from "../../hooks/queries";
import type { IncidentDetail, Workflow } from "../../types/api";
import { Badge, Button, ErrorState, InlineError, LoadingBlock, StatusBadge, cx, humanize } from "../../components/ui";
import { Indicator } from "../../components/motion";
import { SourceBadge, WorkflowStepper } from "./shared";

export interface IncidentContext {
  wf: Workflow;
  detail: IncidentDetail | undefined;
}

export const useIncidentContext = () => useOutletContext<IncidentContext>();

function ReviewActions({ wf, detail }: IncidentContext) {
  const act = useIncidentAction();
  const [mode, setMode] = useState<null | "verify" | "dismiss">(null);
  const [text, setText] = useState("");
  const inc = wf.incident;
  if (inc.status !== "REPORTED" || inc.run_started_at || !detail || detail.legacy) return null;
  const stale = !detail.is_current_epoch;
  return (
    <div className="panel grid gap-3 p-4">
      <p className="text-sm text-fog-300">
        New report. Review the report and map, then {inc.verified_at ? "run the agents." : "optionally confirm the hazard on record (e.g. after a call-back or field check), dismiss it, or run the agents."}
      </p>
      {stale && <p className="text-xs text-warn">Submitted before the current simulated state was loaded; it cannot be processed.</p>}
      {mode ? (
        <form className="grid gap-2" onSubmit={(e) => {
          e.preventDefault();
          act.mutate({ kind: mode, id: inc.id, text }, { onSuccess: () => { setMode(null); setText(""); } });
        }}>
          <label htmlFor="review-text" className="text-sm text-fog-300">
            {mode === "verify" ? "How was it confirmed? (recorded in the audit trail)" : "Reason for dismissing (internal, not shown to the citizen)"}
          </label>
          <textarea id="review-text" rows={2} value={text} onChange={(e) => setText(e.target.value)} maxLength={500}
            required={mode === "dismiss"} minLength={mode === "dismiss" ? 3 : undefined}
            className="rounded-xl border border-white/10 bg-ink-900 px-3 py-2 text-sm text-fog-100 focus:border-lilac-300/60 focus:outline-none" />
          <div className="flex gap-2">
            <Button type="submit" variant={mode === "dismiss" ? "danger" : "primary"} loading={act.isPending}>
              {mode === "verify" ? "Confirm hazard" : "Dismiss report"}
            </Button>
            <Button type="button" variant="ghost" onClick={() => setMode(null)}>Cancel</Button>
          </div>
        </form>
      ) : (
        <div className="flex flex-wrap gap-2">
          <Button onClick={() => act.mutate({ kind: "run", id: inc.id })} loading={act.isPending && act.variables?.kind === "run"} disabled={stale}>
            <Play size={15} weight="fill" /> Run agent workflow
          </Button>
          {!inc.verified_at && <Button variant="ghost" onClick={() => setMode("verify")} disabled={stale}><CheckCircle size={15} /> Confirm hazard</Button>}
          <Button variant="danger" onClick={() => setMode("dismiss")}><Prohibit size={15} /> Dismiss</Button>
        </div>
      )}
      <InlineError error={act.error} />
    </div>
  );
}

const OPEN_PLAN = ["DRAFT", "REVISION_REQUIRED", "AWAITING_APPROVAL", "APPROVED", "STALE"];

/** Close a finished emergency. The backend withdraws unexecuted plans and keeps the full record. */
function ResolveAction({ wf, detail }: IncidentContext) {
  const resolve = useResolve();
  const [confirming, setConfirming] = useState(false);
  const [note, setNote] = useState("");
  const inc = wf.incident;
  if (!detail || detail.legacy) return null;
  if (inc.status === "RESOLVED") {
    return (
      <div className="panel flex items-start gap-3 p-4 text-sm" role="status">
        <FlagCheckered size={18} className="mt-0.5 shrink-0 text-ok" />
        <p className="text-fog-300">
          Resolved{detail.resolved_by ? ` by ${detail.resolved_by}` : ""}
          {inc.resolved_at ? ` on ${new Date(inc.resolved_at).toLocaleString()}` : ""}. It no longer appears in the active
          queue or maps; this record and its audit trail are kept.
          {inc.resolution_note ? <span className="mt-1 block text-fog-400">Note: {inc.resolution_note}</span> : null}
        </p>
      </div>
    );
  }
  if (inc.status === "DISMISSED" || wf.running || (inc.status === "REPORTED" && !inc.run_started_at)) return null;
  const pending = wf.plans.filter((p) => OPEN_PLAN.includes(p.status));
  return (
    <div className="panel grid gap-3 p-4">
      {confirming ? (
        <form className="grid gap-2" onSubmit={(e) => {
          e.preventDefault();
          if (resolve.isPending) return;
          resolve.mutate({ id: inc.id, note: note.trim() || undefined, expectedStatus: inc.status },
            { onSuccess: () => { setConfirming(false); setNote(""); } });
        }}>
          <p className="text-sm text-fog-200">Resolve this emergency?</p>
          <ul className="list-disc pl-5 text-xs text-fog-400">
            <li>It leaves the active queue and the maps. The report, plans and audit trail are kept.</li>
            {pending.length > 0 && <li className="text-warn">Unexecuted plan {pending.map((p) => `v${p.version}`).join(", ")} will be withdrawn; nothing is executed.</li>}
            <li>Simulated road closures stay in place until lifted separately.</li>
          </ul>
          <label htmlFor="resolve-note" className="text-sm text-fog-300">Resolution note (optional, internal)</label>
          <textarea id="resolve-note" rows={2} value={note} onChange={(e) => setNote(e.target.value)} maxLength={500}
            className="rounded-xl border border-white/10 bg-ink-900 px-3 py-2 text-sm text-fog-100 focus:border-lilac-300/60 focus:outline-none" />
          <div className="flex gap-2">
            <Button type="submit" loading={resolve.isPending} disabled={resolve.isPending}>
              <FlagCheckered size={15} /> Resolve emergency
            </Button>
            <Button type="button" variant="ghost" onClick={() => setConfirming(false)} disabled={resolve.isPending}>Cancel</Button>
          </div>
        </form>
      ) : (
        <div className="flex flex-wrap items-center justify-between gap-3">
          <p className="text-sm text-fog-300">
            {inc.status === "EXECUTED" ? "Response recorded. Resolve the emergency when it is over." : "Resolve the emergency if it no longer needs a response."}
          </p>
          <Button variant="ghost" onClick={() => setConfirming(true)}><FlagCheckered size={15} /> Resolve emergency</Button>
        </div>
      )}
      <InlineError error={resolve.error} />
    </div>
  );
}

export default function IncidentLayout() {
  const { id = "" } = useParams();
  const location = useLocation();
  const wf = useWorkflow(id);
  const running = Boolean(wf.data?.running);
  const detail = useIncident(id, running);

  if (wf.isError) return <ErrorState error={wf.error} onRetry={() => wf.refetch()} />;
  if (!wf.data) return <LoadingBlock rows={4} />;
  const w = wf.data;
  const inc = w.incident;
  const latest = w.plans[w.plans.length - 1];
  const tab = ({ isActive }: { isActive: boolean }) =>
    cx("relative inline-flex h-10 items-center gap-2 px-1 text-sm transition",
      isActive ? "text-fog-100" : "text-fog-400 hover:text-fog-100");

  return (
    <div className="grid grid-cols-1 gap-6">
      <div>
        <Link to="/ops" className="inline-flex items-center gap-1.5 text-xs text-fog-400 hover:text-fog-100"><ArrowLeft size={13} /> Operations</Link>
        <div className="mt-3 flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <h1 className="font-display text-2xl font-light tracking-[-0.02em] text-fog-100">
                {humanize(inc.category ?? "incident")}
                {detail.data?.assessment?.location_name ? ` near ${detail.data.assessment.location_name}` : ""}
              </h1>
              <StatusBadge status={inc.status} />
              {inc.reference && <span className="font-mono text-sm text-lilac-200">{inc.reference}</span>}
              <SourceBadge source={inc.source} />
              {inc.urgency && <Badge tone={inc.urgency === "critical" ? "bad" : inc.urgency === "high" ? "warn" : "neutral"}>{inc.urgency} urgency</Badge>}
              {inc.verified_at && <Badge tone="accent">confirmed by {detail.data?.verified_by ?? "operator"}</Badge>}
            </div>
            <p className="mt-1.5 max-w-[90ch] text-sm text-fog-400">{inc.status_reason}</p>
            {detail.data?.legacy && <p className="mt-1 text-xs text-warn">Legacy record from the earlier fictional city. Read-only.</p>}
          </div>
          {running && (
            <span className="inline-flex items-center gap-2 text-sm text-lilac-200" role="status">
              <CircleNotch size={16} className="animate-spin motion-reduce:animate-none" /> Agents working
            </span>
          )}
        </div>
        <div className="mt-5"><WorkflowStepper wf={w} /></div>
      </div>

      <ReviewActions wf={w} detail={detail.data} />
      <ResolveAction wf={w} detail={detail.data} />

      <nav aria-label="Incident sections" className="relative isolate flex gap-6 overflow-x-auto border-b border-white/[0.07]">
        <Indicator variant="underline" />
        <NavLink end to="" className={tab}>Overview</NavLink>
        <NavLink to="plans" className={tab}>
          Plans and validation {w.plans.length > 0 && <span className="text-xs text-fog-500">{w.plans.length} {w.plans.length === 1 ? "version" : "versions"}</span>}
        </NavLink>
        <NavLink to="approval" className={tab}>
          Approval {latest?.status === "AWAITING_APPROVAL" && <span className="h-1.5 w-1.5 rounded-full bg-lilac-300" aria-label="action needed" />}
        </NavLink>
        <NavLink to="audit" className={tab}>Audit trail</NavLink>
      </nav>

      {/* Keyed by incident and tab: tab state (selected revision, open confirmations, notes) never carries
          over from a previously viewed incident, and each tab switch gets a short entrance. */}
      <div key={`${inc.id}:${location.pathname}`} className="tab-enter min-w-0">
        <Outlet context={{ wf: w, detail: detail.data } satisfies IncidentContext} />
      </div>
    </div>
  );
}
