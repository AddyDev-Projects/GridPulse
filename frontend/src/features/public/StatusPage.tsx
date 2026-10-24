import { useState } from "react";
import { Link, useSearchParams } from "react-router";
import { ArrowClockwise, MagnifyingGlass } from "@phosphor-icons/react";
import { useCity, usePublicClosures, usePublicIncidents, usePublicRecentIncidents, useReportStatus } from "../../hooks/queries";
import { Badge, Button, Empty, ErrorState, Panel, Skeleton, humanize, timeAgo, when } from "../../components/ui";
import { COLORS, CityMap, MapLegend } from "../map/CityMap";

const TONE: Record<string, "ok" | "warn" | "bad" | "info" | "accent" | "neutral"> = {
  received: "neutral", under_review: "info", confirmed: "accent", action_recorded: "ok", reviewed: "neutral",
  escalated: "warn", closed: "neutral",
};

function Lookup() {
  const [params, setParams] = useSearchParams();
  const ref = params.get("ref");
  const [input, setInput] = useState(ref ?? "");
  const status = useReportStatus(ref);
  return (
    <Panel title="Track a report">
      <form className="flex flex-col gap-2 sm:flex-row" onSubmit={(e) => { e.preventDefault(); if (input.trim()) setParams({ ref: input.trim().toUpperCase() }); }}>
        <label htmlFor="ref" className="sr-only">Report reference</label>
        <input id="ref" value={input} onChange={(e) => setInput(e.target.value)} placeholder="GP-XXXXXXXX" maxLength={20}
          className="h-11 flex-1 rounded-xl border border-white/10 bg-ink-900 px-4 font-mono text-sm uppercase text-fog-100 placeholder:text-fog-500 focus:border-lilac-300/60 focus:outline-none" />
        <Button type="submit"><MagnifyingGlass size={15} /> Look up</Button>
      </form>
      {ref && (
        <div className="mt-4" aria-live="polite">
          {status.isLoading && <Skeleton className="h-20 w-full" />}
          {status.isError && <ErrorState error={status.error} />}
          {status.data && (
            <div className="rounded-xl bg-white/[0.03] p-4">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-mono text-lilac-200">{status.data.reference}</span>
                <Badge tone={TONE[status.data.status] ?? "neutral"}>{humanize(status.data.status)}</Badge>
                <span className="text-xs text-fog-500">{humanize(status.data.category)} · reported <time dateTime={status.data.submitted_at} title={when(status.data.submitted_at)}>{timeAgo(status.data.submitted_at)}</time></span>
              </div>
              <p className="mt-2 text-sm text-fog-100">{status.data.status_label}</p>
              {status.data.public_note && <p className="mt-2 text-sm text-warn">{status.data.public_note}</p>}
              <p className="mt-2 text-xs text-fog-500">Last updated {timeAgo(status.data.updated_at)}</p>
            </div>
          )}
        </div>
      )}
    </Panel>
  );
}

export default function StatusPage() {
  const city = useCity();
  const incidents = usePublicIncidents();
  const recent = usePublicRecentIncidents();
  const closures = usePublicClosures();
  return (
    <div className="grid gap-6">
      <div>
        <h1 className="font-display text-3xl font-light tracking-[-0.03em] text-fog-100">Report status and safety map</h1>
        <p className="mt-1 max-w-[75ch] text-sm text-fog-400">
          The map shows only incidents an operator has confirmed or acted on, at about 100 m precision, and roads closed by
          approved plans. All closures are part of the GridPulse simulation, not real traffic orders.
        </p>
      </div>
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.6fr)]">
        <div className="grid min-w-0 content-start gap-6">
          <Lookup />
          <Panel
            title="Five latest reports"
            action={<Button variant="ghost" className="h-8 px-3 text-xs" onClick={() => recent.refetch()} loading={recent.isFetching}><ArrowClockwise size={13} /> Refresh</Button>}
          >
            {recent.isError ? <ErrorState error={recent.error} onRetry={() => recent.refetch()} />
              : recent.isLoading ? <Skeleton className="h-28 w-full" />
              : recent.data?.length === 0 ? <Empty title="No public reports yet" />
              : (
                <ol className="divide-y divide-white/[0.06]">
                  {(recent.data ?? []).map((i) => (
                    <li key={i.reference} className="py-3 first:pt-0 last:pb-0">
                      <Link to={`/status?ref=${encodeURIComponent(i.reference)}`} className="block rounded-lg transition hover:bg-white/[0.03] focus-visible:px-2">
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="font-mono text-xs text-lilac-200">{i.reference}</span>
                          <Badge tone={TONE[i.status] ?? "neutral"}>{humanize(i.category)}</Badge>
                          <time dateTime={i.reported_at} title={when(i.reported_at)} className="text-xs text-fog-500">{timeAgo(i.reported_at)}</time>
                        </div>
                        <p className="mt-1 text-xs text-fog-400">{i.location}</p>
                        <p className="mt-0.5 text-xs text-fog-500">{i.status_label}</p>
                      </Link>
                    </li>
                  ))}
                </ol>
              )}
            <p className="mt-3 text-[11px] leading-relaxed text-fog-500">Public summaries are deliberately coarse. Police and other sensitive reports do not expose allegations or exact locations.</p>
          </Panel>
          <Panel title="Confirmed incidents">
            {incidents.isError ? <ErrorState error={incidents.error} onRetry={() => incidents.refetch()} />
              : !incidents.data ? <Skeleton className="h-24 w-full" />
              : incidents.data.length === 0 ? <Empty title="No confirmed incidents right now" />
              : (
                <ul className="divide-y divide-white/[0.06]">
                  {incidents.data.map((i) => (
                    <li key={i.reference} className="py-3 text-sm">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="text-fog-100">{humanize(i.category)}</span>
                        <Badge tone={TONE[i.status] ?? "neutral"}>{humanize(i.status)}</Badge>
                        {i.simulated_scenario && <Badge tone="warn">simulated demo</Badge>}
                      </div>
                      {i.advisory && <p className="mt-1 text-xs text-warn">{i.advisory}</p>}
                      <p className="mt-0.5 text-xs text-fog-500">Updated {timeAgo(i.updated_at)}</p>
                    </li>
                  ))}
                </ul>
              )}
          </Panel>
        </div>
        <div className="grid min-w-0 content-start gap-3">
          {city.data ? (
            <CityMap city={city.data} className="h-[420px] sm:h-[600px]" closures={closures.data ?? []}
              markers={(incidents.data ?? []).map((i) => ({ id: i.reference, lat: i.lat, lon: i.lon,
                label: humanize(i.category), detail: i.status_label }))} ariaLabel="Map of confirmed incidents in Mangaluru" />
          ) : city.isError ? <ErrorState error={city.error} /> : <Skeleton className="h-[420px] w-full sm:h-[600px]" />}
          <MapLegend items={[
            { color: COLORS.incident, label: "Confirmed incident (approximate)", dot: true },
            { color: COLORS.closed, label: "Closed road (simulated)", dashed: true },
            { color: COLORS.caution, label: "Caution (simulated)", dashed: true },
          ]} />
        </div>
      </div>
    </div>
  );
}
