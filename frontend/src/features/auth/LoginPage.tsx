import { useState } from "react";
import { Link, Navigate, useNavigate, useSearchParams } from "react-router";
import { LockKey } from "@phosphor-icons/react";
import { useLogin, useMe } from "../../hooks/queries";
import { BrandMark } from "../landing/LandingPage";
import { Button, InlineError } from "../../components/ui";

/** Only allow redirects back into the console (prevents open redirects via ?next=). */
export function safeNext(next: string | null): string {
  return next && next.startsWith("/ops") && !next.startsWith("//") ? next : "/ops";
}

export default function LoginPage() {
  const [params] = useSearchParams();
  const next = safeNext(params.get("next"));
  const me = useMe();
  const login = useLogin();
  const navigate = useNavigate();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");

  if (me.data) return <Navigate to={next} replace />;
  const expired = params.get("expired") === "1";

  return (
    <div className="grid min-h-[100dvh] place-items-center bg-ink-950 bg-[radial-gradient(70%_50%_at_50%_0%,rgba(180,162,251,0.1),transparent)] px-4">
      <div className="w-full max-w-sm">
        <Link to="/" className="mb-8 inline-block" aria-label="GridPulse home"><BrandMark /></Link>
        <form
          className="panel grid gap-4 p-6"
          onSubmit={(e) => {
            e.preventDefault();
            login.mutate({ username, password }, { onSuccess: () => navigate(next, { replace: true }) });
          }}
        >
          <div className="flex items-center gap-2">
            <LockKey size={20} className="text-lilac-200" />
            <h1 className="font-display text-xl font-light text-fog-100">Operator sign-in</h1>
          </div>
          {expired && <p className="rounded-lg bg-warn/[0.08] px-3 py-2 text-xs text-warn">Your session ended. Sign in again to continue.</p>}
          <div className="grid gap-2">
            <label htmlFor="username" className="text-sm text-fog-300">Username</label>
            <input id="username" autoComplete="username" value={username} onChange={(e) => setUsername(e.target.value)} required
              className="h-11 rounded-xl border border-white/10 bg-ink-900 px-4 text-sm text-fog-100 focus:border-lilac-300/60 focus:outline-none" />
          </div>
          <div className="grid gap-2">
            <label htmlFor="password" className="text-sm text-fog-300">Password</label>
            <input id="password" type="password" autoComplete="current-password" value={password}
              onChange={(e) => setPassword(e.target.value)} required
              className="h-11 rounded-xl border border-white/10 bg-ink-900 px-4 text-sm text-fog-100 focus:border-lilac-300/60 focus:outline-none" />
          </div>
          <InlineError error={login.error} />
          <Button type="submit" loading={login.isPending} disabled={!username || !password}>Sign in</Button>
          <p className="text-xs leading-relaxed text-fog-500">
            Operator accounts are created by an administrator with the GridPulse CLI. There is no self sign-up.
            Citizens do not need an account: <Link to="/report" className="text-lilac-200 underline-offset-4 hover:underline">report a hazard</Link>.
          </p>
        </form>
      </div>
    </div>
  );
}
