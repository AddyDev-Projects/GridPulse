import { useEffect, useRef, useState } from "react";
import { Microphone, MicrophoneSlash, StopCircle } from "@phosphor-icons/react";
import { cx } from "../../components/ui";

/**
 * Speak-to-type for the report description, using the browser's own speech recognition (Web Speech API).
 * No paid service, no key, nothing recorded or stored by GridPulse: recognised text is appended to the
 * ordinary, editable description field and the report is never submitted automatically.
 * Where the browser has no speech recognition, a short note replaces the button.
 */

interface RecognitionResult { isFinal: boolean; 0: { transcript: string } }
interface RecognitionEvent { resultIndex: number; results: ArrayLike<RecognitionResult> }
interface Recognition {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  onresult: ((e: RecognitionEvent) => void) | null;
  onerror: ((e: { error: string }) => void) | null;
  onend: (() => void) | null;
  start: () => void;
  stop: () => void;
  abort: () => void;
}
type RecognitionCtor = new () => Recognition;

export function speechRecognitionCtor(): RecognitionCtor | null {
  if (typeof window === "undefined") return null;
  const w = window as unknown as { SpeechRecognition?: RecognitionCtor; webkitSpeechRecognition?: RecognitionCtor };
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null;
}

const ERRORS: Record<string, string> = {
  "not-allowed": "Microphone access was denied. You can allow it in the browser's site settings, or type instead.",
  "service-not-allowed": "This browser does not allow speech recognition here. Please type instead.",
  "audio-capture": "No microphone was found. Please type instead.",
  "no-speech": "No speech was detected. Try again, or type instead.",
  network: "The browser's speech service could not be reached. Please type instead.",
  "language-not-supported": "Speech recognition is not available for this language. Please type instead.",
};

export function SpeechInput({ onText, describedBy }: { onText: (text: string) => void; describedBy?: string }) {
  const Ctor = speechRecognitionCtor();
  const [listening, setListening] = useState(false);
  const [interim, setInterim] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  const rec = useRef<Recognition | null>(null);
  const heard = useRef(false);
  const onTextRef = useRef(onText);
  useEffect(() => { onTextRef.current = onText; }, [onText]);
  useEffect(() => () => rec.current?.abort(), []); // never keep the microphone open after leaving the page

  if (!Ctor) {
    return (
      <p className="flex items-center gap-1.5 text-xs text-fog-500" data-testid="speech-unsupported">
        <MicrophoneSlash size={14} /> Voice input is not available in this browser. Please type your description.
      </p>
    );
  }

  const start = () => {
    setMessage(null);
    setInterim("");
    heard.current = false;
    let r: Recognition;
    try {
      r = new Ctor();
    } catch {
      setMessage("Speech recognition could not start. Please type instead.");
      return;
    }
    r.lang = navigator.language || "en-IN";
    r.continuous = true;
    r.interimResults = true;
    r.onresult = (e) => {
      let live = "";
      for (let i = e.resultIndex; i < e.results.length; i++) {
        const text = e.results[i][0].transcript.trim();
        if (!text) continue;
        if (e.results[i].isFinal) {
          heard.current = true;
          onTextRef.current(text);
        } else {
          live += `${text} `;
        }
      }
      setInterim(live.trim());
    };
    r.onerror = (e) => {
      if (e.error !== "aborted") setMessage(ERRORS[e.error] ?? "Speech recognition stopped unexpectedly. Please type instead.");
    };
    r.onend = () => {
      setListening(false);
      setInterim("");
      rec.current = null;
      setMessage((m) => m ?? (heard.current ? "Transcribed. Check and edit the text before sending." : "Nothing was transcribed. Try again, or type instead."));
    };
    rec.current = r;
    try {
      r.start();
      setListening(true);
    } catch {
      rec.current = null;
      setMessage("Speech recognition could not start. Please type instead.");
    }
  };
  const stop = () => rec.current?.stop();

  return (
    <div className="grid gap-1">
      <button type="button" onClick={listening ? stop : start} aria-pressed={listening} aria-describedby={describedBy}
        className={cx("inline-flex h-9 w-fit items-center gap-1.5 rounded-full px-3.5 text-[13px] ring-1 ring-inset transition focus-visible:outline-2 focus-visible:outline-lilac-300",
          listening ? "bg-bad/15 text-fog-100 ring-bad/40" : "text-fog-300 ring-white/15 hover:text-fog-100")}>
        {listening ? <StopCircle size={16} weight="fill" className="text-bad" /> : <Microphone size={16} />}
        {listening ? "Stop listening" : "Speak instead"}
      </button>
      <p className="text-xs text-fog-500" role="status" aria-live="polite">
        {listening ? (interim ? `Listening… "${interim}"` : "Listening… speak now. Your browser's speech service converts it to text.") : message}
      </p>
    </div>
  );
}
