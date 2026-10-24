import { useEffect, useState } from "react";
import { Link } from "react-router";
import {
  ArrowRight,
  List,
  X,
  ShieldCheck,
  UserCheck,
  Database,
  Graph,
  Brain,
  ArrowsClockwise,
  Lightning,
  TrafficSignal,
  Path,
} from "@phosphor-icons/react";
import { GlobeBackdrop } from "./GlobeBackdrop";
import { cx } from "../../components/ui";

const NAV = [
  { label: "Overview", href: "#top" },
  { label: "How It Works", href: "#how" },
  { label: "Report a hazard", to: "/report" },
  { label: "Live map", to: "/status" },
  { label: "Architecture", href: "#architecture" },
];

export function BrandMark({ className }: { className?: string }) {
  return (
    <span className={cx("inline-flex items-center gap-2.5", className)}>
      <span className="relative grid h-8 w-8 place-items-center rounded-[10px] bg-white/[0.06] ring-1 ring-inset ring-white/12">
        <span className="font-display text-[15px] font-semibold italic tracking-[-0.08em] text-lilac-200">//</span>
        <span className="absolute -right-0.5 -top-0.5 h-2 w-2 rounded-full bg-orchid-300" />
      </span>
      <span className="font-display text-[17px] font-semibold tracking-tight text-fog-100">GridPulse</span>
    </span>
  );
}

function LandingNav() {
  const [open, setOpen] = useState(false);
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  const linkCls = "whitespace-nowrap rounded-full px-3.5 py-2 text-[13.5px] text-fog-300 transition hover:bg-white/[0.06] hover:text-fog-100";
  return (
    <header className="enter relative z-20" style={{ ["--d" as string]: "0ms" }}>
      <nav aria-label="Primary" className="mx-auto flex h-[72px] max-w-[1280px] items-center justify-between px-4 sm:px-6 lg:px-10">
        <a href="#top" aria-label="GridPulse home">
          <BrandMark />
        </a>
        <div className="glass hidden items-center gap-0.5 rounded-full p-1 lg:flex">
          {NAV.map((n) =>
            n.to ? (
              <Link key={n.label} to={n.to} className={linkCls}>
                {n.label}
              </Link>
            ) : (
              <a key={n.label} href={n.href} className={linkCls}>
                {n.label}
              </a>
            ),
          )}
        </div>
        <div className="flex items-center gap-2">
          <span className="hidden rounded-full border border-white/10 px-3 py-1.5 font-mono text-[10.5px] tracking-[0.16em] text-fog-400 xl:inline">
            SIMULATION ENVIRONMENT
          </span>
          <Link
            to="/ops"
            className="hidden h-10 items-center gap-2 whitespace-nowrap rounded-full bg-fog-100 px-5 text-[13.5px] font-medium text-ink-950 transition hover:bg-white active:scale-[0.98] sm:inline-flex"
          >
            Operator console <ArrowRight size={15} weight="bold" />
          </Link>
          <button
            type="button"
            className="glass grid h-10 w-10 place-items-center rounded-full lg:hidden"
            aria-label={open ? "Close menu" : "Open menu"}
            aria-expanded={open}
            aria-controls="mobile-menu"
            onClick={() => setOpen((o) => !o)}
          >
            {open ? <X size={18} /> : <List size={18} />}
          </button>
        </div>
      </nav>
      {open && (
        <div id="mobile-menu" className="glass absolute inset-x-4 top-[72px] z-30 rounded-2xl p-2 lg:hidden">
          {NAV.map((n) =>
            n.to ? (
              <Link key={n.label} to={n.to} className="block rounded-xl px-4 py-3 text-sm text-fog-100 hover:bg-white/5">
                {n.label}
              </Link>
            ) : (
              <a key={n.label} href={n.href} onClick={() => setOpen(false)} className="block rounded-xl px-4 py-3 text-sm text-fog-100 hover:bg-white/5">
                {n.label}
              </a>
            ),
          )}
          <Link to="/ops" className="mt-1 flex h-11 items-center justify-center gap-2 rounded-xl bg-fog-100 text-sm font-medium text-ink-950">
            Operator console <ArrowRight size={15} weight="bold" />
          </Link>
        </div>
      )}
    </header>
  );
}

function Hero() {
  return (
    <section id="top" className="relative isolate flex min-h-[100dvh] flex-col overflow-hidden">
      <GlobeBackdrop />
      <LandingNav />
      <div className="relative z-10 mx-auto flex w-full max-w-[1280px] flex-1 flex-col items-center px-4 pb-[34vh] pt-10 text-center sm:px-6 sm:pt-14 lg:pt-16">
        <p className="enter glass inline-flex items-center gap-2 rounded-full px-4 py-1.5 text-[12.5px] text-lilac-200" style={{ ["--d" as string]: "180ms" }}>
          <span aria-hidden className="text-orchid-300">✦</span> Agentic AI for the Cities of Tomorrow
        </p>
        <h1
          className="enter mt-6 max-w-[16ch] font-display text-[38px] font-light leading-[1.08] tracking-[-0.035em] text-fog-100 sm:max-w-[20ch] sm:text-[52px] lg:max-w-none lg:text-[64px]"
          style={{ ["--d" as string]: "300ms" }}
        >
          When Cities Face the Unexpected, <br className="hidden lg:block" />
          <span className="text-gradient font-normal">Intelligence Must Work Together.</span>
        </h1>
        <p className="enter mt-6 max-w-[56ch] text-[15px] leading-relaxed text-fog-300 sm:text-base" style={{ ["--d" as string]: "520ms" }}>
          GridPulse coordinates intelligent agents, infrastructure dependencies, and human decisions to simulate safer
          emergency response on Mangaluru's real road network.
        </p>
        <div className="enter mt-9 flex flex-col items-center gap-3 sm:flex-row" style={{ ["--d" as string]: "720ms" }}>
          <Link
            to="/report"
            className="group inline-flex h-12 items-center gap-2.5 rounded-full bg-fog-100 px-7 text-[14.5px] font-medium text-ink-950 shadow-[0_10px_40px_-10px_rgba(207,194,255,0.55)] transition hover:bg-white active:scale-[0.98]"
          >
            Report a hazard
            <ArrowRight size={16} weight="bold" className="transition-transform duration-300 group-hover:translate-x-0.5" />
          </Link>
          <Link
            to="/ops"
            className="glass inline-flex h-12 items-center gap-2 rounded-full px-6 text-[14.5px] text-fog-100 transition hover:bg-white/10 active:scale-[0.98]"
          >
            Operator console
          </Link>
        </div>
      </div>
    </section>
  );
}

const FLOW = [
  { verb: "Report", agent: "Citizen", text: "A resident pins a hazard on the map of Mangaluru and describes it. No account needed." },
  { verb: "Triage", agent: "Sentinel", text: "Structures the report, separates facts from inferences, and pulls synthetic sensor evidence." },
  { verb: "Propose", agent: "Mobility + Traffic + Grid", text: "Mobility routes responders, AnalyseTraffic checks disruption and route conflicts, and Grid models utility dependencies." },
  { verb: "Validate", agent: "Validator", text: "Deterministic rules check every route, dependency, target and piece of evidence." },
  { verb: "Revise", agent: "Coordinator", text: "Findings go back to the responsible agent. A new plan version is checked again." },
  { verb: "Decide", agent: "Operator", text: "A human approves or rejects the exact plan version. Only then does the simulated city change." },
];

function HowItWorks() {
  return (
    <section id="how" className="relative mx-auto max-w-[1280px] scroll-mt-8 px-4 py-24 sm:px-6 lg:px-10 lg:py-32">
      <h2 className="max-w-[20ch] font-display text-3xl font-light tracking-[-0.03em] text-fog-100 sm:text-[44px] sm:leading-[1.1]">
        Six moves from a citizen report to a <span className="text-gradient">safe, authorized</span> response.
      </h2>
      <ol className="mt-14 grid gap-px overflow-hidden rounded-2xl border border-white/[0.07] bg-white/[0.07] sm:grid-cols-2 lg:grid-cols-3">
        {FLOW.map((f) => (
          <li key={f.verb} className="group relative bg-ink-900 p-6 transition hover:bg-ink-850 lg:p-8">
            <span className="font-display text-2xl font-medium tracking-tight text-fog-100">{f.verb}</span>
            <p className="mt-1 text-[13px] text-lilac-300">{f.agent}</p>
            <p className="mt-4 max-w-[38ch] text-[14.5px] leading-relaxed text-fog-400">{f.text}</p>
          </li>
        ))}
      </ol>
    </section>
  );
}

function Chip({ icon, label, sub, tone }: { icon: React.ReactNode; label: string; sub: string; tone: "grid" | "mob" | "bad" | "ok" }) {
  const ring = { grid: "ring-volt-400/40", mob: "ring-lilac-300/40", bad: "ring-bad/50", ok: "ring-ok/45" }[tone];
  return (
    <div className={cx("flex items-center gap-3 rounded-2xl bg-ink-850 px-4 py-3 ring-1 ring-inset", ring)}>
      <span className="grid h-9 w-9 place-items-center rounded-xl bg-white/[0.05] text-fog-100">{icon}</span>
      <div className="text-left">
        <p className="text-sm font-medium text-fog-100">{label}</p>
        <p className="text-xs text-fog-500">{sub}</p>
      </div>
    </div>
  );
}

function ConflictStory() {
  return (
    <section id="simulation" className="relative border-y border-white/[0.06] bg-ink-900/60">
      <div className="mx-auto grid max-w-[1280px] items-center gap-14 px-4 py-24 sm:px-6 lg:grid-cols-[1fr_1.05fr] lg:px-10 lg:py-28">
        <div>
          <h2 className="font-display text-3xl font-light tracking-[-0.03em] text-fog-100 sm:text-[42px] sm:leading-[1.1]">
            Two agents, each right on their own. Together, unsafe.
          </h2>
          <p className="mt-6 max-w-[52ch] text-[15px] leading-relaxed text-fog-400">
            Utility isolates a flooded feeder zone. Mobility routes the fire crew across the signalised junction on
            George Fernandes Road. Neither knows that, in the simulated power model, that signal is fed only by the
            isolated zone. The validator traces the dependency, blocks the plan, and Mobility reroutes on the real road
            graph before anyone is asked to approve.
          </p>
          <Link to="/ops" className="mt-8 inline-flex items-center gap-2 text-sm font-medium text-lilac-200 underline-offset-4 hover:underline">
            See it in the operator console <ArrowRight size={15} />
          </Link>
        </div>
        <figure className="panel relative p-6 sm:p-8">
          <div className="grid gap-3">
            <Chip icon={<Lightning size={18} />} label="Feeder zone 2B isolated" sub="Utility proposal (simulated power model)" tone="grid" />
            <div className="ml-7 h-5 w-px bg-white/15" aria-hidden />
            <Chip icon={<TrafficSignal size={18} />} label="Signal on George Fernandes Road goes dark" sub="Real OSM signal; simulated feed" tone="bad" />
            <div className="ml-7 h-5 w-px bg-white/15" aria-hidden />
            <Chip icon={<Path size={18} />} label="Fire-station route crosses that junction" sub="Mobility proposal, plan v1" tone="mob" />
            <div className="my-2 flex items-center gap-3 text-xs text-fog-500">
              <span className="h-px flex-1 bg-white/10" /> validator blocks v1, coordinator revises <span className="h-px flex-1 bg-white/10" />
            </div>
            <Chip icon={<ArrowsClockwise size={18} />} label="Plan v2 reroutes on the road graph" sub="Revalidated, then sent for approval" tone="ok" />
          </div>
          <figcaption className="mt-6 text-xs text-fog-500">Demo scenario A: an illustrative location on real OpenStreetMap roads. Sensors and power network are simulated.</figcaption>
        </figure>
      </div>
    </section>
  );
}

function Architecture() {
  return (
    <section id="architecture" className="mx-auto max-w-[1280px] scroll-mt-8 px-4 py-24 sm:px-6 lg:px-10 lg:py-32">
      <h2 className="max-w-[22ch] font-display text-3xl font-light tracking-[-0.03em] text-fog-100 sm:text-[44px] sm:leading-[1.1]">
        Language models interpret. Deterministic code decides.
      </h2>
      <div className="mt-14 grid gap-4 md:grid-cols-6">
        <article className="relative overflow-hidden rounded-2xl border border-white/[0.07] bg-[radial-gradient(120%_120%_at_0%_0%,rgba(180,162,251,0.16),transparent_55%),var(--color-ink-900)] p-7 md:col-span-4">
          <ShieldCheck size={26} className="text-lilac-200" />
          <h3 className="mt-5 font-display text-xl font-medium text-fog-100">Deterministic validator</h3>
          <p className="mt-2 max-w-[54ch] text-sm leading-relaxed text-fog-400">
            Seven explicit rules: road safety, infrastructure dependencies, action allowlists, approval, stale state,
            evidence quality and unresolved conflicts. No model can override a blocking finding.
          </p>
          <div className="mt-6 flex flex-wrap gap-2">
            {["R1 Road safety", "R2 Dependencies", "R3 Allowlist", "R4 Approval", "R5 Stale state", "R6 Evidence", "R7 Escalation"].map((r) => (
              <span key={r} className="rounded-full bg-white/[0.05] px-3 py-1 font-mono text-[11px] text-fog-300 ring-1 ring-inset ring-white/10">
                {r}
              </span>
            ))}
          </div>
        </article>
        <article className="rounded-2xl border border-white/[0.07] bg-ink-900 p-7 md:col-span-2">
          <UserCheck size={26} className="text-orchid-300" />
          <h3 className="mt-5 font-display text-xl font-medium text-fog-100">Human approval</h3>
          <p className="mt-2 text-sm leading-relaxed text-fog-400">
            Bound to one plan version, its content hash and the city state. Any change voids it.
          </p>
        </article>
        <article className="rounded-2xl border border-white/[0.07] bg-ink-900 p-7 md:col-span-2">
          <Brain size={26} className="text-volt-400" />
          <h3 className="mt-5 font-display text-xl font-medium text-fog-100">Agents</h3>
          <p className="mt-2 text-sm leading-relaxed text-fog-400">
             Sentinel triages reports with an LLM or transparent fallback rules. Mobility, AnalyseTraffic and Grid plan on deterministic NetworkX-backed data.
          </p>
        </article>
        <article className="rounded-2xl border border-white/[0.07] bg-[linear-gradient(160deg,rgba(106,168,255,0.12),transparent_60%),var(--color-ink-900)] p-7 md:col-span-2">
          <Graph size={26} className="text-volt-400" />
          <h3 className="mt-5 font-display text-xl font-medium text-fog-100">City graph</h3>
          <p className="mt-2 text-sm leading-relaxed text-fog-400">
            Real OpenStreetMap roads of Mangaluru, one-way aware. Routes are computed on the graph, never generated.
          </p>
        </article>
        <article className="rounded-2xl border border-white/[0.07] bg-ink-900 p-7 md:col-span-2">
          <Database size={26} className="text-ok" />
          <h3 className="mt-5 font-display text-xl font-medium text-fog-100">PostgreSQL audit</h3>
          <p className="mt-2 text-sm leading-relaxed text-fog-400">
            Every plan version, finding, revision, decision and execution is persisted and replayable.
          </p>
        </article>
      </div>
    </section>
  );
}

export default function LandingPage() {
  return (
    <div className="min-h-[100dvh] overflow-x-hidden bg-ink-950 text-fog-100">
      <Hero />
      <main>
        <HowItWorks />
        <ConflictStory />
        <Architecture />
        <section className="mx-auto max-w-[1280px] px-4 pb-24 sm:px-6 lg:px-10">
          <div className="relative overflow-hidden rounded-3xl border border-white/[0.08] bg-[radial-gradient(80%_140%_at_50%_120%,rgba(243,184,245,0.18),transparent_60%),var(--color-ink-900)] px-6 py-16 text-center">
            <h2 className="mx-auto max-w-[24ch] font-display text-3xl font-light tracking-[-0.03em] text-fog-100 sm:text-4xl">
              See a hazard? Pin it on the map. Operators take it from there.
            </h2>
            <Link
              to="/report"
              className="mt-8 inline-flex h-12 items-center gap-2.5 rounded-full bg-fog-100 px-7 text-[14.5px] font-medium text-ink-950 transition hover:bg-white active:scale-[0.98]"
            >
              Report a hazard <ArrowRight size={16} weight="bold" />
            </Link>
          </div>
        </section>
      </main>
      <footer className="border-t border-white/[0.06]">
        <div className="mx-auto flex max-w-[1280px] flex-col gap-4 px-4 py-10 text-sm text-fog-500 sm:flex-row sm:items-center sm:justify-between sm:px-6 lg:px-10">
          <BrandMark />
          <p className="max-w-[70ch]">
            Hackathon prototype. Fictional city and synthetic sensor data only. GridPulse is not connected to any real
            infrastructure, dispatch system or sensor network.
          </p>
        </div>
      </footer>
    </div>
  );
}
