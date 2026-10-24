import { useState } from "react";
import { Link, useNavigate } from "react-router";
import { ArrowRight, ArrowCounterClockwise, PaperPlaneTilt, CaretRight, Siren } from "@phosphor-icons/react";
import {
  useGraph,
  useIncidents,
  useRecentAudit,
  useResetSimulation,
  useScenarios,
  useSimState,
  useSubmitIncident,
} from "../../hooks/queries";
import {
  Badge,
  Button,
  Empty,
  ErrorState,
  InlineError,
  LoadingBlock,
  Panel,
  SeverityIcon,
  Skeleton,
  StatusBadge,
  cx,
  humanize,
  timeAgo,
} from "../../components/ui";
import { CityGraph } from "../simulation/CityGraph";

function ScenarioPicker() {
  const scenarios = useScenarios();
  const state = useSimState();
  const reset = useResetSimulation();
  const [selected, setSelected] = useState<string | null>(null);
  const active = state.data?.active_scenario.key;
  const chosen = selected ?? active;

  if (scenarios.isError) return <ErrorState error={scenarios.error} onRetry={() => scenarios.refetch()} />;
  if (!scenarios.data || !state.data) return <Skeleton className="h-28 w-full" />;

  const pick = scenarios.data.find((s) => s.key === chosen);
  return (
    <div>
      <fieldset className="min-w-0">
        <legend className="mb-3 text-sm text-fog-400">Scenario</legend>
        <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
          {scenarios.data.map((s) => (
            <label
              key={s.key}
              className={cx(
                "relative cursor-pointer rounded-xl border p-3.5 transition focus-within:ring-2 focus-within:ring-lilac-300",
                chosen === s.key ? "border-lilac-300/50 bg-lilac-300/[0.06]" : "border-white/[0.08] bg-white/[0.02] hover:border-white/15",
              )}
            >
              <input type="radio" name="scenario" value={s.key} checked={chosen === s.key} onChange={() => setSelected(s.key)} className="sr-only" />
              <span className="flex items-center justify-between">
                <span className="font-mono text-xs text-fog-500">Scenario {s.code}</span>
                {active === s.key && <Badge tone="ok">Loaded</Badge>}
              </span>
              <span className="mt-1.5 block text-[13.5px] font-medium leading-snug text-fog-100">{s.title}</span>
            </label>
          ))}
        </div>
      </fieldset>
      {pick && (
        <div className="mt-4 flex flex-col gap-3 rounded-xl bg-white/[0.025] p-4 sm:flex-row sm:items-start sm:justify-between">
          <div className="text-sm">
            <p className="text-fog-300">{pick.summary}</p>
            <p className="mt-1.5 text-fog-500">Expected: {pick.expected_outcome}</p>
          </div>
          <Button
            variant={chosen === active ? "ghost" : "primary"}
            className="shrink-0"
            loading={reset.isPending}
            onClick={() => reset.mutate(pick.key, { onSuccess: () => setSelected(null) })}
          >
            <ArrowCounterClockwise size={15} /> {chosen === active ? "Reset city" : `Load scenario ${pick.code}`}
          </Button>
        </div>
      )}
      <div className="mt-2">
        <InlineError error={reset.error} />
      </div>
    </div>
  );
}

function ReportForm({ sampleReport, sampleHint }: { sampleReport: string; sampleHint: string }) {
  const submit = useSubmitIncident();
  const navigate = useNavigate();
  // Remounted (via key) whenever the loaded scenario changes, so the synthetic sample prefills cleanly.
  const [report, setReport] = useState(sampleReport);
  const [hint, setHint] = useState(sampleHint);
  const [alias, setAlias] = useState("Resident, Foundry Ave");

  const tooShort = report.trim().length < 10;
  return (
    <form
      className="grid gap-4"
      onSubmit={(e) => {
        e.preventDefault();
        if (tooShort) return;
        submit.mutate(
          { report_text: report.trim(), location_hint: hint.trim() || undefined, reporter_alias: alias.trim() || undefined },
          { onSuccess: (inc) => navigate(`/ops/incidents/${inc.id}`) },
        );
      }}
    >
      <div className="grid gap-2">
        <label htmlFor="report" className="text-sm text-fog-300">
          Citizen report <span className="text-fog-500">(synthetic, editable)</span>
        </label>
        <textarea
          id="report"
          value={report}
          onChange={(e) => setReport(e.target.value)}
          rows={4}
          maxLength={2000}
          className="w-full resize-y rounded-xl border border-white/10 bg-ink-900 px-4 py-3 text-[14.5px] leading-relaxed text-fog-100 placeholder:text-fog-500 focus:border-lilac-300/60 focus:outline-none"
          aria-describedby="report-help"
        />
        <p id="report-help" className="text-xs text-fog-500">
          {report.length}/2000 characters. Treated as untrusted input; Sentinel only extracts structure from it.
        </p>
      </div>
      <div className="grid gap-4 sm:grid-cols-2">
        <div className="grid gap-2">
          <label htmlFor="hint" className="text-sm text-fog-300">Location hint</label>
          <input id="hint" value={hint} onChange={(e) => setHint(e.target.value)} maxLength={120}
            className="h-11 rounded-xl border border-white/10 bg-ink-900 px-4 text-sm text-fog-100 focus:border-lilac-300/60 focus:outline-none" />
        </div>
        <div className="grid gap-2">
          <label htmlFor="alias" className="text-sm text-fog-300">Reporter alias</label>
          <input id="alias" value={alias} onChange={(e) => setAlias(e.target.value)} maxLength={80}
            className="h-11 rounded-xl border border-white/10 bg-ink-900 px-4 text-sm text-fog-100 focus:border-lilac-300/60 focus:outline-none" />
        </div>
      </div>
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <InlineError error={submit.error} />
        <Button type="submit" disabled={tooShort} loading={submit.isPending} className="sm:ml-auto">
          <PaperPlaneTilt size={16} /> Submit and run agents
        </Button>
      </div>
    </form>
  );
}

function IncidentList() {
  const incidents = useIncidents("current");
  if (incidents.isError) return <ErrorState error={incidents.error} onRetry={() => incidents.refetch()} />;
  if (!incidents.data) return <LoadingBlock rows={3} />;
  if (incidents.data.length === 0)
    return <Empty title="No incidents in this simulation run">Submit the citizen report above to start the agent workflow.</Empty>;
  return (
    <ul className="divide-y divide-white/[0.06]">
      {incidents.data.map((i) => (
        <li key={i.id}>
          <Link to={`/ops/incidents/${i.id}`} className="group flex items-center gap-4 rounded-lg px-2 py-3.5 transition hover:bg-white/[0.03]">
            <Siren size={20} className={i.urgency === "critical" ? "text-bad" : "text-fog-500"} />
            <div className="min-w-0 flex-1">
              <p className="truncate text-sm text-fog-100">{i.report_text}</p>
              <p className="mt-0.5 text-xs text-fog-500">
                {i.reporter_alias} · {timeAgo(i.created_at)}
                {i.current_plan_version ? ` · plan v${i.current_plan_version}` : ""}
              </p>
            </div>
            <StatusBadge status={i.status} />
            <CaretRight size={16} className="text-fog-500 transition group-hover:translate-x-0.5" />
          </Link>
        </li>
      ))}
    </ul>
  );
}

function CityStatus() {
  const state = useSimState();
  const graph = useGraph();
  if (state.isError) return <ErrorState error={state.error} onRetry={() => state.refetch()} />;
  if (!state.data) return <LoadingBlock rows={2} />;
  const s = state.data;
  return (
    <div className="grid gap-4">
      {graph.data ? (
        <Link to="/ops/city" aria-label="Open the full city graph" className="block rounded-xl bg-ink-950/60 ring-1 ring-white/[0.06] transition hover:ring-white/15">
          <CityGraph
            graph={graph.data}
            compact
            showSensors={false}
            routes={graph.data.active_routes.map((r) => ({ node_path: r.node_path, label: r.name, tone: "active" as const }))}
          />
        </Link>
      ) : (
        <Skeleton className="h-48 w-full" />
      )}
      <div>
        <p className="mb-2 text-xs text-fog-500">Infrastructure not in normal state</p>
        {s.alerts.length === 0 ? (
          <p className="text-sm text-fog-400">All simulated infrastructure is operating normally.</p>
        ) : (
          <ul className="grid gap-1.5">
            {s.alerts.map((a) => (
              <li key={a.id} className="flex items-center justify-between gap-3 text-sm">
                <span className="truncate text-fog-300">{a.name}</span>
                <StatusBadge status={a.status.toUpperCase()} />
              </li>
            ))}
          </ul>
        )}
      </div>
      {s.active_routes.length > 0 && (
        <div className="rounded-xl bg-ok/[0.06] p-3 text-sm ring-1 ring-inset ring-ok/20">
          <p className="text-ok">Active evacuation route</p>
          <p className="mt-1 font-mono text-xs text-fog-300">{s.active_routes[0].node_path.join(" → ")}</p>
        </div>
      )}
    </div>
  );
}

function RecentEvents() {
  const audit = useRecentAudit();
  if (audit.isError) return <ErrorState error={audit.error} />;
  if (!audit.data) return <LoadingBlock rows={3} />;
  if (audit.data.length === 0) return <Empty title="No events yet" />;
  return (
    <ol className="grid gap-3">
      {audit.data.map((e) => (
        <li key={e.id} className="flex gap-3 text-sm">
          <SeverityIcon severity={e.severity} size={16} />
          <div className="min-w-0">
            <p className="line-clamp-2 text-fog-300">{e.message}</p>
            <p className="mt-0.5 text-xs text-fog-500">
              {humanize(e.event_type.replace(".", " "))} · {timeAgo(e.created_at)}
            </p>
          </div>
        </li>
      ))}
    </ol>
  );
}

export default function DashboardPage() {
  const state = useSimState();
  const s = state.data;
  return (
    <div className="grid grid-cols-1 gap-6">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h1 className="font-display text-3xl font-light tracking-[-0.03em] text-fog-100">Operations</h1>
          <p className="mt-1 text-sm text-fog-400">Meridian Bay, Sector 4. A fictional city with synthetic sensor data.</p>
        </div>
        {s && (
          <dl className="flex gap-6 text-sm">
            <div>
              <dt className="text-xs text-fog-500">Pending approvals</dt>
              <dd className={cx("font-display text-2xl", s.pending_approvals ? "text-lilac-200" : "text-fog-300")}>{s.pending_approvals}</dd>
            </div>
            <div>
              <dt className="text-xs text-fog-500">Incidents this run</dt>
              <dd className="font-display text-2xl text-fog-300">{Object.values(s.incidents_by_status).reduce((a, b) => a + b, 0)}</dd>
            </div>
            <div>
              <dt className="text-xs text-fog-500">City state</dt>
              <dd className="font-display text-2xl text-fog-300">v{s.state_version}</dd>
            </div>
          </dl>
        )}
      </div>
      {state.isError && <ErrorState error={state.error} onRetry={() => state.refetch()} />}

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-12">
        <div className="grid min-w-0 grid-cols-1 content-start gap-6 lg:col-span-8">
          <Panel title="Report an incident">
            <div className="grid grid-cols-1 gap-6">
              <ScenarioPicker />
              <div className="h-px bg-white/[0.06]" />
              {s ? (
                <ReportForm
                  key={`${s.active_scenario.key}-${s.epoch}`}
                  sampleReport={s.active_scenario.sample_report}
                  sampleHint={s.active_scenario.sample_location_hint}
                />
              ) : (
                <Skeleton className="h-40 w-full" />
              )}
            </div>
          </Panel>
          <Panel title="Incidents" action={<span className="text-xs text-fog-500">Current simulation run</span>}>
            <IncidentList />
          </Panel>
        </div>
        <div className="grid min-w-0 grid-cols-1 content-start gap-6 lg:col-span-4">
          <Panel title="City status" action={<Link to="/ops/city" className="inline-flex items-center gap-1 text-xs text-lilac-200">Full graph <ArrowRight size={12} /></Link>}>
            <CityStatus />
          </Panel>
          <Panel title="Recent events">
            <RecentEvents />
          </Panel>
        </div>
      </div>
    </div>
  );
}
