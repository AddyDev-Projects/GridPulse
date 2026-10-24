import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router";
import { ArrowCounterClockwise, CheckCircle, Copy, Crosshair, HandPointing, MapPin, X } from "@phosphor-icons/react";
import { useCity, useSubmitReport } from "../../hooks/queries";
import { Button, ErrorState, InlineError, Panel, Skeleton, cx, humanize } from "../../components/ui";
import { CityMap } from "../map/CityMap";
import type { City, LatLng, PublicReportReceipt } from "../../types/api";
import { SpeechInput } from "./SpeechInput";

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

type GeoStatus = "locating" | "detected" | "outside" | "denied" | "unavailable" | "error";

const inCoverage = (city: City | undefined, [lat, lon]: LatLng) => {
  if (!city) return true;
  const [s, w, n, e] = city.coverage_bbox;
  return lat >= s && lat <= n && lon >= w && lon <= e;
};

/**
 * One-shot browser geolocation (no background tracking). The position stays in the page until the
 * report is submitted; a manual pick always wins and is never replaced by a late geolocation result.
 */
function useReportLocation(city: City | undefined) {
  const [raw, setRaw] = useState<LatLng | null>(null);
  const [manual, setManual] = useState<LatLng | null>(null);
  const [geoState, setGeoState] = useState<"locating" | "found" | "denied" | "unavailable" | "error">(
    () => (typeof navigator !== "undefined" && navigator.geolocation ? "locating" : "unavailable"));
  const [picking, setPicking] = useState(false);

  // One browser request, no watchPosition. Only the async callbacks set state.
  const request = useCallback(() => {
    navigator.geolocation?.getCurrentPosition(
      (pos) => {
        const p: LatLng = [Number(pos.coords.latitude.toFixed(6)), Number(pos.coords.longitude.toFixed(6))];
        if (!Number.isFinite(p[0]) || !Number.isFinite(p[1])) return setGeoState("error");
        setRaw(p);
        setGeoState("found");
      },
      (err) => setGeoState(err.code === 1 ? "denied" : "error"),
      { enableHighAccuracy: true, timeout: 10_000, maximumAge: 60_000 },
    );
  }, []);
  useEffect(() => { request(); }, [request]);
  const detect = () => {
    if (!navigator.geolocation) return;
    setGeoState("locating");
    request();
  };

  // Coverage is judged once both the position and the city's mapped area are known.
  const outside = raw !== null && city !== undefined && !inCoverage(city, raw);
  const detected = raw !== null && city !== undefined && !outside ? raw : null;
  const status: GeoStatus = geoState === "found" ? (city === undefined ? "locating" : outside ? "outside" : "detected") : geoState;

  const pick = useCallback((p: LatLng) => {
    setManual(p);
    setPicking(false);
  }, []);
  const useDetected = () => { setManual(null); setPicking(false); };
  const reset = () => { setManual(null); setPicking(false); };
  // A manual pick always wins; a late geolocation answer only updates `raw`, never `manual`.
  return { location: manual ?? detected, manual, detected, status, picking, setPicking, pick, useDetected, detect, reset };
}

const GEO_TEXT: Record<GeoStatus, string> = {
  locating: "Finding your location… (your browser may ask for permission)",
  detected: "Using your detected location.",
  outside: "Your detected location is outside the area GridPulse has mapped. Pick the hazard's location on the map.",
  denied: "Location permission was not granted. Pick the location on the map.",
  unavailable: "This browser cannot share its location. Pick the location on the map.",
  error: "Your location could not be determined. Try again, or pick the location on the map.",
};

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
  const [website, setWebsite] = useState("");
  const loc = useReportLocation(city.data);
  const picked = loc.location;
  const [touched, setTouched] = useState(false);
  const appendSpeech = useCallback((text: string) => {
    setDescription((d) => (d.trim() ? `${d.trimEnd()} ${text}` : text).slice(0, 2000));
  }, []);

  if (city.isError) return <ErrorState error={city.error} onRetry={() => city.refetch()} />;
  if (submit.data) {
    return <Receipt r={submit.data} onAnother={() => { submit.reset(); setDescription(""); setIndicators([]); loc.reset(); setTouched(false); }} />;
  }

  const descError = description.trim().length < 10 ? "Describe what you see in at least 10 characters." : null;
  const pinError = picked ? null : "Choose a location: allow location access, or pick it on the map.";
  const canSubmit = !descError && !pinError;

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
              location_description: where.trim() || undefined, website: website || undefined,
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
            <div className="flex flex-wrap items-end justify-between gap-2">
              <label htmlFor="description" className="text-sm text-fog-300">Describe what you see</label>
              <SpeechInput onText={appendSpeech} describedBy="description-help" />
            </div>
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

          <div className="grid gap-2">
            <label htmlFor="where" className="text-sm text-fog-300">Landmark <span className="text-fog-500">(optional)</span></label>
            <input id="where" value={where} maxLength={200} onChange={(e) => setWhere(e.target.value)}
              className="h-11 rounded-xl border border-white/10 bg-ink-900 px-4 text-sm text-fog-100 focus:border-lilac-300/60 focus:outline-none" />
            <p className="text-xs text-fog-500">The time is recorded automatically when you send the report.</p>
          </div>

          {/* Honeypot for bots: hidden from people and assistive tech. */}
          <div aria-hidden="true" className="absolute -left-[9999px] h-px w-px overflow-hidden">
            <label htmlFor="website">Website</label>
            <input id="website" tabIndex={-1} autoComplete="off" value={website} onChange={(e) => setWebsite(e.target.value)} />
          </div>

          <div className="grid gap-1 rounded-xl bg-white/[0.03] p-3 text-sm" data-testid="location-summary">
            <p className={cx("flex items-center gap-2", picked ? "text-fog-300" : touched ? "text-bad" : "text-fog-400")}>
              <MapPin size={16} />
              {picked
                ? `${loc.manual ? "Location picked on the map" : "Detected location"}: ${picked[0].toFixed(5)}, ${picked[1].toFixed(5)}`
                : pinError}
            </p>
            <p className="text-xs text-fog-500">Your location is only sent when you send the report.</p>
          </div>

          <InlineError error={submit.error} />
          <div className="flex flex-wrap gap-2">
            <Button type="submit" loading={submit.isPending}>Send report</Button>
          </div>
        </form>

        <div className="order-first grid min-w-0 content-start gap-3 lg:order-none">
          <div className="grid gap-2">
            <p role="status" aria-live="polite" className={cx("text-sm",
              loc.manual ? "text-fog-300" : loc.status === "detected" ? "text-ok" : loc.status === "locating" ? "text-fog-400" : "text-warn")}>
              {loc.picking ? "Click the map where the hazard is." : loc.manual ? "Using the location you picked on the map." : GEO_TEXT[loc.status]}
            </p>
            <div className="flex flex-wrap gap-2">
              {loc.picking ? (
                <Button type="button" variant="ghost" className="h-9 px-4" onClick={() => loc.setPicking(false)}>
                  <X size={15} /> Cancel picking
                </Button>
              ) : (
                <Button type="button" variant="ghost" className="h-9 px-4" onClick={() => loc.setPicking(true)}>
                  <HandPointing size={15} /> {loc.manual ? "Pick a different location" : "Pick location on map"}
                </Button>
              )}
              {loc.manual && loc.detected && !loc.picking && (
                <Button type="button" variant="ghost" className="h-9 px-4" onClick={loc.useDetected}>
                  <ArrowCounterClockwise size={15} /> Use detected location
                </Button>
              )}
              {!loc.manual && !loc.picking && (loc.status === "error" || loc.status === "denied") && (
                <Button type="button" variant="ghost" className="h-9 px-4" onClick={loc.detect}>
                  <Crosshair size={15} /> Try detecting again
                </Button>
              )}
            </div>
          </div>
          {city.data ? (
            <CityMap city={city.data} className={cx("h-[360px] sm:h-[520px]", loc.picking && "ring-2 ring-lilac-300")}
              picked={picked} onPick={loc.picking ? loc.pick : undefined} focus={picked ? [picked] : null} scrollZoom
              ariaLabel={loc.picking ? "Map of Mangaluru: click to choose the report location" : "Map of Mangaluru"} />
          ) : (
            <Skeleton className="h-[360px] w-full sm:h-[520px]" />
          )}
          <p className="text-xs text-fog-500">The dashed outline is the area GridPulse has mapped. Reports outside it are not accepted.</p>
        </div>
      </div>
    </div>
  );
}
