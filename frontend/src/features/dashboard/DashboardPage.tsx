import { useState } from "react";
import { Link, useNavigate } from "react-router";
import { ArrowRight, ArrowCounterClockwise, CaretRight, Flask, Siren } from "@phosphor-icons/react";
import {
  useCity,
  useClearSimulation,
  useIncidents,
  useLoadScenario,
  useMe,
  useOverlay,
  useRecentAudit,
  useScenarios,
  useSimState,
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
import { COLORS, CityMap, MapLegend } from "../map/CityMap";
import { SourceBadge } from "../incidents/shared";

function ReportQueue() {
  const incidents = useIncidents("current");
  const navigate = useNavigate();
  if (incidents.isError) return <ErrorState error={incidents.error} onRetry={() => incidents.refetch()} />;
  if (!incidents.data) return <LoadingBlock rows={3} />;
  if (incidents.data.length === 0)
    return (
      <Empty title="No reports yet">
        Citizens report at <Link to="/report" className="text-lilac-200 underline underline-offset-4">/report</Link>. Admins can
        also load a labelled demo scenario.
      </Empty>
    );
  const order = (s: string) => (s === "REPORTED" ? 0 : s === "AWAITING_APPROVAL" || s === "STALE" ? 1 : 2);
  const rows = [...incidents.data].sort((a, b) => order(a.status) - order(b.status));
  return (
    <ul className="divide-y divide-white/[0.06]">
      {rows.map((i) => (
        <li key={i.id}>
          <button type="button" onClick={() => navigate(`/ops/incidents/${i.id}`)}
            className="group flex w-full items-center gap-4 rounded-lg px-2 py-3.5 text-left transition hover:bg-white/[0.03]">
            <Siren size={20} className={i.status === "REPORTED" ? "text-orchid-300" : "text-fog-500"} />
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-2 text-xs">
                <span className="font-mono text-lilac-200">{i.reference}</span>
                <span className="text-fog-300">{humanize(i.category ?? "other")}</span>
                <SourceBadge source={i.source} />
                {i.verified_at && <Badge tone="accent">confirmed</Badge>}
              </div>
              <p className="mt-1 truncate text-sm text-fog-100">{i.report_text}</p>
              <p className="mt-0.5 text-xs text-fog-500">
                {timeAgo(i.created_at)}{i.current_plan_version ? ` · plan v${i.current_plan_version}` : ""}
                {!i.is_current_epoch ? " · before the current simulated state" : ""}
              </p>
            </div>
            <StatusBadge status={i.status} />
            <CaretRight size={16} className="text-fog-500 transition group-hover:translate-x-0.5" />
          </button>
        </li>
      ))}
    </ul>
  );
}

function ScenarioControls() {
  const me = useMe();
  const scenarios = useScenarios();
  const state = useSimState();
  const load = useLoadScenario();
  const clear = useClearSimulation();
  const [confirm, setConfirm] = useState<string | null>(null);
  const isAdmin = me.data?.role === "admin";
  if (scenarios.isError) return <ErrorState error={scenarios.error} />;
  if (!scenarios.data || !state.data) return <Skeleton className="h-32 w-full" />;
  const active = state.data.active_scenario?.key;
  return (
    <div className="grid gap-3">
      <p className="text-xs leading-relaxed text-fog-500">
        Demo scenarios add SYNTHETIC sensors (and sometimes simulated closures) at illustrative real locations and
        create a labelled simulated report. Loading one lifts current simulated closures and makes earlier plans stale.
      </p>
      {!isAdmin && <p className="rounded-lg bg-white/[0.03] px-3 py-2 text-xs text-fog-400">Loading scenarios requires the admin role.</p>}
      <ul className="grid gap-2">
        {scenarios.data.map((s) => (
          <li key={s.key} className={cx("rounded-xl border p-3", active === s.key ? "border-lilac-300/40 bg-lilac-300/[0.05]" : "border-white/[0.07]")}>
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <p className="text-sm text-fog-100"><span className="font-mono text-xs text-fog-500">{s.code}</span> {s.title}</p>
                <p className="mt-0.5 text-xs text-fog-500">{s.expected_outcome}</p>
              </div>
              {active === s.key ? <Badge tone="ok">loaded</Badge> : isAdmin && (
                confirm === s.key ? (
                  <div className="flex shrink-0 gap-1">
                    <Button className="h-8 px-3 text-xs" loading={load.isPending}
                      onClick={() => load.mutate(s.key, { onSettled: () => setConfirm(null) })}>Load</Button>
                    <Button variant="ghost" className="h-8 px-3 text-xs" onClick={() => setConfirm(null)}>Cancel</Button>
                  </div>
                ) : (
                  <Button variant="ghost" className="h-8 shrink-0 px-3 text-xs" onClick={() => setConfirm(s.key)}>
                    <Flask size={13} /> Load
                  </Button>
                )
              )}
            </div>
          </li>
        ))}
      </ul>
      {isAdmin && (
        <Button variant="ghost" className="justify-self-start" loading={clear.isPending} onClick={() => clear.mutate()}>
          <ArrowCounterClockwise size={14} /> Clear simulated state
        </Button>
      )}
      <InlineError error={load.error ?? clear.error} />
    </div>
  );
}

function OverviewMap() {
  const city = useCity();
  const overlay = useOverlay();
  const incidents = useIncidents("current");
  if (!city.data) return <Skeleton className="h-80 w-full" />;
  const markers = (incidents.data ?? []).filter((i) => i.lat != null && i.lon != null).map((i) => ({
    id: i.id, lat: i.lat!, lon: i.lon!, label: `${i.reference} ${humanize(i.category ?? "other")}`, detail: humanize(i.status),
    color: i.status === "REPORTED" ? COLORS.incident : i.status === "EXECUTED" ? COLORS.route : "#cfc2ff",
  }));
  return (
    <div className="grid gap-3">
      <CityMap city={city.data} className="h-80" markers={markers} closures={overlay.data?.closures ?? []}
        signals={overlay.data?.nodes.filter((n) => n.kind === "signal") ?? []}
        routes={(overlay.data?.active_routes ?? []).map((r) => ({ id: `r${r.id}`, geometry: r.geometry, label: r.name, tone: "route" as const }))} />
      <MapLegend items={[
        { color: COLORS.incident, label: "New report", dot: true },
        { color: COLORS.closed, label: "Closed (simulated)", dashed: true },
        { color: COLORS.route, label: "Active route" },
        { color: COLORS.signalOk, label: "OSM signal (simulated power)", dot: true },
      ]} />
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
            <p className="mt-0.5 text-xs text-fog-500">{humanize(e.event_type.replace(".", " "))} · {e.actor} · {timeAgo(e.created_at)}</p>
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
          <p className="mt-1 text-sm text-fog-400">Mangaluru, Karnataka. Real OpenStreetMap roads; simulated sensors, utilities and actions.</p>
        </div>
        {s && (
          <dl className="flex gap-6 text-sm">
            {([["New reports", s.new_reports, s.new_reports > 0], ["Awaiting approval", s.pending_approvals, s.pending_approvals > 0],
              ["Closed segments", s.closures, false], ["City state", `v${s.state_version}`, false]] as const).map(([k, v, hot]) => (
              <div key={k}>
                <dt className="text-xs text-fog-500">{k}</dt>
                <dd className={cx("font-display text-2xl", hot ? "text-lilac-200" : "text-fog-300")}>{v}</dd>
              </div>
            ))}
          </dl>
        )}
      </div>
      {state.isError && <ErrorState error={state.error} onRetry={() => state.refetch()} />}
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-12">
        <div className="grid min-w-0 grid-cols-1 content-start gap-6 lg:col-span-7">
          <Panel title="Report queue" action={<span className="text-xs text-fog-500">New reports first</span>}>
            <ReportQueue />
          </Panel>
          <Panel title="Recent events"><RecentEvents /></Panel>
        </div>
        <div className="grid min-w-0 grid-cols-1 content-start gap-6 lg:col-span-5">
          <Panel title="City overview" action={<Link to="/ops/city" className="inline-flex items-center gap-1 text-xs text-lilac-200">Full map <ArrowRight size={12} /></Link>}>
            <OverviewMap />
          </Panel>
          <Panel title="Demo scenarios"><ScenarioControls /></Panel>
        </div>
      </div>
    </div>
  );
}
