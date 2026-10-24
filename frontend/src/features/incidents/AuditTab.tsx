import { useState } from "react";
import { CaretDown } from "@phosphor-icons/react";
import { useIncidentContext } from "./IncidentLayout";
import { useIncidentAudit } from "../../hooks/queries";
import { Empty, ErrorState, LoadingBlock, Panel, SeverityIcon, clock, cx, humanize } from "../../components/ui";
import type { AuditEvent } from "../../types/api";

function EventRow({ e }: { e: AuditEvent }) {
  const [open, setOpen] = useState(false);
  const hasDetails = Object.keys(e.details).length > 0;
  return (
    <li className="relative pl-9">
      <span className="absolute left-0 top-0.5 grid h-6 w-6 place-items-center rounded-full bg-ink-850 ring-1 ring-white/10">
        <SeverityIcon severity={e.severity} size={14} />
      </span>
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5">
        <span className="font-mono text-xs text-fog-500">{clock(e.created_at)}</span>
        <span className="text-sm font-medium text-fog-100">{humanize(e.event_type.replace(".", " "))}</span>
        <span className="text-xs text-fog-500">{e.actor}</span>
      </div>
      <p className="mt-1 text-[13.5px] leading-relaxed text-fog-300">{e.message}</p>
      {hasDetails && (
        <>
          <button onClick={() => setOpen((o) => !o)} aria-expanded={open}
            className="mt-1 inline-flex items-center gap-1 text-xs text-fog-500 hover:text-fog-300">
            <CaretDown size={12} className={cx("transition", open && "rotate-180")} /> {open ? "Hide" : "Show"} recorded details
          </button>
          {open && (
            <pre className="scrollbar-thin mt-2 max-h-72 overflow-auto rounded-xl bg-ink-950 p-3 font-mono text-[11px] leading-relaxed text-fog-400 ring-1 ring-white/[0.06]">
              {JSON.stringify(e.details, null, 2)}
            </pre>
          )}
        </>
      )}
    </li>
  );
}

export default function AuditTab() {
  const { wf } = useIncidentContext();
  const audit = useIncidentAudit(wf.incident.id, wf.running);
  return (
    <Panel title="Audit trail" action={<span className="text-xs text-fog-500">Persisted in PostgreSQL, append-only</span>}>
      {audit.isError ? (
        <ErrorState error={audit.error} onRetry={() => audit.refetch()} />
      ) : !audit.data ? (
        <LoadingBlock rows={5} />
      ) : audit.data.length === 0 ? (
        <Empty title="No events recorded" />
      ) : (
        <ol className="relative grid gap-6 before:absolute before:bottom-2 before:left-3 before:top-2 before:w-px before:bg-white/[0.07]">
          {audit.data.map((e) => <EventRow key={e.id} e={e} />)}
        </ol>
      )}
    </Panel>
  );
}
