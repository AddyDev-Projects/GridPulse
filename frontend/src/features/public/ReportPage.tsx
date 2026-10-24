import { useState } from "react";
import { Link } from "react-router";
import { CheckCircle, Crosshair, Copy, MapPin } from "@phosphor-icons/react";
import { useCity, useSubmitReport } from "../../hooks/queries";
import { Button, ErrorState, InlineError, Panel, Skeleton, cx, humanize } from "../../components/ui";
import { CityMap } from "../map/CityMap";
import type { LatLng, PublicReportReceipt } from "../../types/api";

const CATEGORY_LABEL: Record<string, string> = {
  flood: "Flooding / waterlogging",
  fire: "Fire",
  road_obstruction: "Road blocked (tree, debris, collapse)",
  power_outage: "Power lines / electrical hazard",
  medical_emergency: "Medical emergency",
  accident: "Road collision / accident",
  rescue: "Rescue request",
  police_assistance: "Police assistance",
  violence_assault: "Violence or assault",
  homicide: "Homicide / suspected homicide",
  threat_weapon: "Threat or weapon reported",
  theft_robbery_burglary: "Theft, robbery or burglary",
  suspicious_activity: "Suspicious activity",
  other: "Something else",
};
const INDICATOR_LABEL: Record<string, string> = {
  people_trapped: "People trapped",
  injuries: "Someone is injured",
  multiple_injured: "Several people are injured",
  person_unconscious: "Someone appears unconscious",
  vehicles_stranded: "Vehicles stuck",
  water_rising: "Water is rising",
  power_lines_down: "Power lines down",
  road_blocked: "Road completely blocked",
  fire_spreading: "Fire is spreading",
  structural_damage: "Building or wall damaged",
  weapon_present: "Weapon seen or reported",
  suspected_homicide: "Possible homicide reported",
  suspect_present: "Person involved may still be present",
};

function toIsoWithZone(local: string): string | undefined {
  if (!local) return undefined;
  const d = new Date(local);
  return Number.isNaN(d.getTime()) ? undefined : d.toISOString();
}

function Receipt({ r, onAnother }: { r: PublicReportReceipt; onAnother: () => void }) {
  const [copied, setCopied] = useState(false);
  return (
    <Panel className="mx-auto max-w-xl text-center">
      <CheckCircle size={44} weight="fill" className="mx-auto text-ok" />
      <h1 className="mt-4 font-display text-2xl font-light text-fog-100">Report received</h1>
      <p className="mt-2 text-sm text-fog-400">Keep this reference to check what happens next. No account or contact details are needed.</p>
      <div className="mt-6 inline-flex items-center gap-3 rounded-2xl bg-white/[0.04] px-5 py-3 ring-1 ring-white/10">
        <span className="font-mono text-2xl tracking-wider text-lilac-200" data-testid="report-reference">{r.reference}</span>
        <button
          type="button"
          onClick={() => navigator.clipboard?.writeText(r.reference).then(() => setCopied(true)).catch(() => undefined)}
          className="text-fog-400 hover:text-fog-100" aria-label="Copy reference"
        >
          <Copy size={18} />
        </button>
      </div>
      {copied && <p className="mt-2 text-xs text-ok">Copied</p>}
      <p className="mt-4 text-sm text-fog-300">Status: {r.status_label}</p>
      {r.nearest_road && <p className="mt-1 text-xs text-fog-500">Matched to the nearest mapped road: {r.nearest_road}</p>}
      <div className="mt-6 flex flex-wrap justify-center gap-2">
        <Link to={`/status?ref=${r.reference}`} className="inline-flex h-10 items-center rounded-full bg-fog-100 px-5 text-sm font-medium text-ink-950">
          Track this report
        </Link>
        <Button variant="ghost" onClick={onAnother}>Report something else</Button>
      </div>
    </Panel>
  );
}

export default function ReportPage() {
  const city = useCity();
  const submit = useSubmitReport();
  const [category, setCategory] = useState("flood");
  const [description, setDescription] = useState("");
  const [indicators, setIndicators] = useState<string[]>([]);
  const [where, setWhere] = useState("");
  const [observed, setObserved] = useState("");
  const [website, setWebsite] = useState("");
  const [picked, setPicked] = useState<LatLng | null>(null);
  const [geoError, setGeoError] = useState<string | null>(null);
  const [touched, setTouched] = useState(false);

  if (city.isError) return <ErrorState error={city.error} onRetry={() => city.refetch()} />;
  if (submit.data) {
    return <Receipt r={submit.data} onAnother={() => { submit.reset(); setDescription(""); setIndicators([]); setPicked(null); setTouched(false); }} />;
  }

  const descError = description.trim().length < 10 ? "Describe what you see in at least 10 characters." : null;
  const pinError = picked ? null : "Tap the map to mark where it is.";
  const canSubmit = !descError && !pinError;

  const useMyLocation = () => {
    setGeoError(null);
    if (!navigator.geolocation) return setGeoError("Your browser cannot share its location. Tap the map instead.");
    navigator.geolocation.getCurrentPosition(
      (p) => setPicked([Number(p.coords.latitude.toFixed(6)), Number(p.coords.longitude.toFixed(6))]),
      () => setGeoError("Location permission was not granted. Tap the map instead."),
      { enableHighAccuracy: true, timeout: 10_000 },
    );
  };

  return (
    <div className="grid gap-6">
      <div>
        <h1 className="font-display text-3xl font-light tracking-[-0.03em] text-fog-100">Report a hazard in Mangaluru</h1>
        <p className="mt-1 max-w-[70ch] text-sm text-fog-400">
          No sign-in needed. Your report goes to the operators' review queue; you get a reference to track it. We do not ask
          for your name or phone number. GridPulse is a reporting and coordination prototype, not a replacement for calling 112.
        </p>
      </div>
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)]">
        <form
          className="panel grid min-w-0 content-start gap-5 p-5"
          noValidate
          onSubmit={(e) => {
            e.preventDefault();
            setTouched(true);
            if (!canSubmit || !picked) return;
            submit.mutate({
              category, description: description.trim(), lat: picked[0], lon: picked[1], indicators,
              location_description: where.trim() || undefined, observed_at: toIsoWithZone(observed), website: website || undefined,
            });
          }}
        >
          <fieldset className="min-w-0">
            <legend className="mb-2 text-sm text-fog-300">What is happening?</legend>
            <div className="grid gap-2 sm:grid-cols-2">
              {(city.data?.categories ?? Object.keys(CATEGORY_LABEL)).map((c) => (
                <label key={c} className={cx("cursor-pointer rounded-xl border px-3.5 py-3 text-sm transition focus-within:ring-2 focus-within:ring-lilac-300",
                  category === c ? "border-lilac-300/50 bg-lilac-300/[0.07] text-fog-100" : "border-white/[0.08] text-fog-300 hover:border-white/15")}>
                  <input type="radio" name="category" value={c} checked={category === c} onChange={() => setCategory(c)} className="sr-only" />
                  {CATEGORY_LABEL[c] ?? humanize(c)}
                </label>
              ))}
            </div>
          </fieldset>

          <div className="grid gap-2">
            <label htmlFor="description" className="text-sm text-fog-300">Describe what you see</label>
            <textarea id="description" rows={4} maxLength={2000} value={description} onChange={(e) => setDescription(e.target.value)}
              aria-invalid={touched && !!descError} aria-describedby="description-help"
              className="w-full resize-y rounded-xl border border-white/10 bg-ink-900 px-4 py-3 text-[14.5px] text-fog-100 focus:border-lilac-300/60 focus:outline-none" />
            <p id="description-help" className={cx("text-xs", touched && descError ? "text-bad" : "text-fog-500")}>
              {touched && descError ? descError : `${description.length}/2000. Avoid personal details about other people.`}
            </p>
          </div>

          <fieldset className="min-w-0">
            <legend className="mb-2 text-sm text-fog-300">Anything urgent? <span className="text-fog-500">(tick all that apply)</span></legend>
            <div className="grid gap-x-4 gap-y-2 sm:grid-cols-2">
              {(city.data?.indicators ?? Object.keys(INDICATOR_LABEL)).map((i) => (
                <label key={i} className="flex items-center gap-2 text-sm text-fog-300">
                  <input type="checkbox" className="h-4 w-4 accent-lilac-300" checked={indicators.includes(i)}
                    onChange={(e) => setIndicators((prev) => (e.target.checked ? [...prev, i] : prev.filter((x) => x !== i)))} />
                  {INDICATOR_LABEL[i] ?? humanize(i)}
                </label>
              ))}
            </div>
          </fieldset>

          <div className="grid gap-4 sm:grid-cols-2">
            <div className="grid gap-2">
              <label htmlFor="where" className="text-sm text-fog-300">Landmark <span className="text-fog-500">(optional)</span></label>
              <input id="where" value={where} maxLength={200} onChange={(e) => setWhere(e.target.value)}
                className="h-11 rounded-xl border border-white/10 bg-ink-900 px-4 text-sm text-fog-100 focus:border-lilac-300/60 focus:outline-none" />
            </div>
            <div className="grid gap-2">
              <label htmlFor="observed" className="text-sm text-fog-300">When did you see it? <span className="text-fog-500">(optional)</span></label>
              <input id="observed" type="datetime-local" value={observed} onChange={(e) => setObserved(e.target.value)}
                className="h-11 rounded-xl border border-white/10 bg-ink-900 px-4 text-sm text-fog-100 focus:border-lilac-300/60 focus:outline-none [color-scheme:dark]" />
            </div>
          </div>

          {/* Honeypot for bots: hidden from people and assistive tech. */}
          <div aria-hidden="true" className="absolute -left-[9999px] h-px w-px overflow-hidden">
            <label htmlFor="website">Website</label>
            <input id="website" tabIndex={-1} autoComplete="off" value={website} onChange={(e) => setWebsite(e.target.value)} />
          </div>

          <div className="grid gap-2 rounded-xl bg-white/[0.03] p-3 text-sm">
            <p className={cx("flex items-center gap-2", picked ? "text-fog-300" : touched ? "text-bad" : "text-fog-400")}>
              <MapPin size={16} />
              {picked ? `Location set: ${picked[0].toFixed(5)}, ${picked[1].toFixed(5)}` : pinError}
            </p>
            {geoError && <p className="text-xs text-warn">{geoError}</p>}
          </div>

          <InlineError error={submit.error} />
          <div className="flex flex-wrap gap-2">
            <Button type="submit" loading={submit.isPending}>Send report</Button>
          </div>
        </form>

        <div className="order-first grid min-w-0 content-start gap-3 lg:order-none">
          <div className="flex items-center justify-between gap-3">
            <p className="text-sm text-fog-300">Tap the map where the hazard is</p>
            <Button type="button" variant="ghost" className="h-9 px-4" onClick={useMyLocation}>
              <Crosshair size={15} /> Use my location
            </Button>
          </div>
          {city.data ? (
            <CityMap city={city.data} className="h-[360px] sm:h-[520px]" picked={picked} onPick={setPicked}
              focus={picked ? [picked] : null} ariaLabel="Map of Mangaluru: tap to choose the report location" />
          ) : (
            <Skeleton className="h-[360px] w-full sm:h-[520px]" />
          )}
          <p className="text-xs text-fog-500">The dashed outline is the area GridPulse has mapped. Reports outside it are not accepted.</p>
        </div>
      </div>
    </div>
  );
}
