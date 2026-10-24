import { NavLink, Outlet, useOutletContext, useParams, Link } from "react-router";
import { ArrowLeft, CircleNotch, Play } from "@phosphor-icons/react";
import { useIncident, useRunIncident, useWorkflow } from "../../hooks/queries";
import type { IncidentDetail, Workflow } from "../../types/api";
import { Badge, Button, ErrorState, InlineError, LoadingBlock, StatusBadge, cx, humanize } from "../../components/ui";
import { WorkflowStepper } from "./shared";

export interface IncidentContext {
  wf: Workflow;
  detail: IncidentDetail | undefined;
}

export const useIncidentContext = () => useOutletContext<IncidentContext>();

export default function IncidentLayout() {
  const { id = "" } = useParams();
  const wf = useWorkflow(id);
  const running = Boolean(wf.data?.running);
  const detail = useIncident(id, running);
  const run = useRunIncident();

  if (wf.isError) return <ErrorState error={wf.error} onRetry={() => wf.refetch()} />;
  if (!wf.data) return <LoadingBlock rows={4} />;
  const w = wf.data;
  const inc = w.incident;
  const latest = w.plans[w.plans.length - 1];
  const tab = ({ isActive }: { isActive: boolean }) =>
    cx(
      "relative inline-flex h-10 items-center gap-2 px-1 text-sm transition",
      isActive ? "text-fog-100 after:absolute after:inset-x-0 after:-bottom-px after:h-0.5 after:rounded-full after:bg-lilac-300" : "text-fog-400 hover:text-fog-100",
    );

  return (
    <div className="grid grid-cols-1 gap-6">
      <div>
        <Link to="/ops" className="inline-flex items-center gap-1.5 text-xs text-fog-400 hover:text-fog-100">
          <ArrowLeft size={13} /> Operations
        </Link>
        <div className="mt-3 flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <h1 className="font-display text-2xl font-light tracking-[-0.02em] text-fog-100">
                {inc.category ? humanize(inc.category) : "Incident"}
                {detail.data?.assessment?.location_name ? ` at ${detail.data.assessment.location_name}` : ""}
              </h1>
              <StatusBadge status={inc.status} />
              {inc.urgency && <Badge tone={inc.urgency === "critical" ? "bad" : inc.urgency === "high" ? "warn" : "neutral"}>{inc.urgency} urgency</Badge>}
              {detail.data && !detail.data.is_current_epoch && <Badge tone="warn">Previous simulation run</Badge>}
            </div>
            <p className="mt-1.5 max-w-[90ch] text-sm text-fog-400">{inc.status_reason}</p>
          </div>
          {running && (
            <span className="inline-flex items-center gap-2 text-sm text-lilac-200" role="status">
              <CircleNotch size={16} className="animate-spin motion-reduce:animate-none" /> Agents working
            </span>
          )}
          {inc.status === "REPORTED" && !inc.run_started_at && (
            <div className="grid gap-2">
              <Button onClick={() => run.mutate(inc.id)} loading={run.isPending}>
                <Play size={15} weight="fill" /> Run agent workflow
              </Button>
              <InlineError error={run.error} />
            </div>
          )}
        </div>
        <div className="mt-5">
          <WorkflowStepper wf={w} />
        </div>
      </div>

      <nav aria-label="Incident sections" className="flex gap-6 overflow-x-auto border-b border-white/[0.07]">
        <NavLink end to="" className={tab}>Overview</NavLink>
        <NavLink to="plans" className={tab}>
          Plans and validation {w.plans.length > 0 && <span className="text-xs text-fog-500">{w.plans.length} {w.plans.length === 1 ? "version" : "versions"}</span>}
        </NavLink>
        <NavLink to="approval" className={tab}>
          Approval {latest?.status === "AWAITING_APPROVAL" && <span className="h-1.5 w-1.5 rounded-full bg-lilac-300" aria-label="action needed" />}
        </NavLink>
        <NavLink to="audit" className={tab}>Audit trail</NavLink>
      </nav>

      <Outlet context={{ wf: w, detail: detail.data } satisfies IncidentContext} />
    </div>
  );
}
