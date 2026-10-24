import { useEffect } from "react";
import { NavLink, Outlet, Link, Navigate, useLocation, useNavigate } from "react-router";
import { SquaresFour, MapTrifold, SignOut, UserCircle } from "@phosphor-icons/react";
import { BrandMark } from "../features/landing/LandingPage";
import { useLogout, useMe, useSimState } from "../hooks/queries";
import { ApiError } from "../services/api";
import { Badge, LoadingBlock, cx } from "../components/ui";

export const UNAUTHENTICATED_EVENT = "gridpulse:unauthenticated";

function Header() {
  const me = useMe();
  const state = useSimState();
  const logout = useLogout();
  const navigate = useNavigate();
  const s = state.data;
  const navCls = ({ isActive }: { isActive: boolean }) =>
    cx("inline-flex h-9 items-center gap-2 rounded-full px-3.5 text-[13px] transition",
      isActive ? "bg-white/[0.09] text-fog-100" : "text-fog-400 hover:bg-white/[0.05] hover:text-fog-100");
  return (
    <header className="sticky top-0 z-[1000] border-b border-white/[0.06] bg-ink-950/85 backdrop-blur-xl">
      <div className="mx-auto flex h-16 max-w-[1440px] items-center gap-3 px-4 sm:px-6">
        <Link to="/" aria-label="GridPulse landing page" className="shrink-0"><BrandMark /></Link>
        <nav aria-label="Console" className="ml-2 flex items-center gap-1 sm:ml-6">
          <NavLink to="/ops" end className={navCls}><SquaresFour size={16} /> <span className="hidden sm:inline">Operations</span></NavLink>
          <NavLink to="/ops/city" className={navCls}><MapTrifold size={16} /> <span className="hidden sm:inline">City map</span></NavLink>
        </nav>
        <div className="ml-auto flex items-center gap-2 text-[11px]">
          {s && (
            <span className="hidden rounded-full bg-white/[0.05] px-2.5 py-1 text-fog-300 ring-1 ring-inset ring-white/10 lg:inline">
              Mangaluru · state v{s.state_version}{s.active_scenario ? ` · demo ${s.active_scenario.code}` : ""}
            </span>
          )}
          {s && (
            <span className="hidden rounded-full bg-white/[0.05] px-2.5 py-1 text-fog-300 ring-1 ring-inset ring-white/10 xl:inline"
              title="Engine Sentinel uses for report triage">
              Sentinel: {s.llm_mode === "fallback" ? "rule-based" : s.llm_mode.replace("_", "-")}
            </span>
          )}
          <span className="rounded-full bg-warn/10 px-2.5 py-1 font-mono tracking-[0.12em] text-warn ring-1 ring-inset ring-warn/30">SIMULATION</span>
          {me.data && (
            <span className="hidden items-center gap-1.5 text-fog-300 md:inline-flex">
              <UserCircle size={16} /> {me.data.username}
              {me.data.role === "admin" && <Badge tone="accent">admin</Badge>}
            </span>
          )}
          <button type="button" onClick={() => logout.mutate(undefined, { onSettled: () => navigate("/operator/login", { replace: true }) })}
            className="inline-flex h-8 items-center gap-1.5 rounded-full px-3 text-fog-400 ring-1 ring-inset ring-white/10 hover:text-fog-100">
            <SignOut size={14} /> <span className="hidden sm:inline">Sign out</span>
          </button>
        </div>
      </div>
    </header>
  );
}

export default function AppShell() {
  const me = useMe();
  const location = useLocation();
  const navigate = useNavigate();

  // Any operator request answered with 401 (expired or revoked session) sends the user to sign in.
  useEffect(() => {
    const onUnauth = () => navigate(`/operator/login?expired=1&next=${encodeURIComponent(location.pathname)}`, { replace: true });
    window.addEventListener(UNAUTHENTICATED_EVENT, onUnauth);
    return () => window.removeEventListener(UNAUTHENTICATED_EVENT, onUnauth);
  }, [navigate, location.pathname]);

  if (me.isLoading) return <div className="mx-auto max-w-[1440px] p-6"><LoadingBlock rows={4} /></div>;
  if (me.error instanceof ApiError && me.error.isUnauthenticated) {
    return <Navigate to={`/operator/login?next=${encodeURIComponent(location.pathname)}`} replace />;
  }
  if (me.isError) {
    return <div className="p-6 text-sm text-bad" role="alert">Cannot reach the GridPulse server. Is the backend running?</div>;
  }
  return (
    <div className="min-h-[100dvh] bg-ink-950 bg-[radial-gradient(90%_50%_at_50%_-10%,rgba(180,162,251,0.08),transparent)] text-fog-100">
      <a href="#main" className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-50 focus:rounded-lg focus:bg-ink-800 focus:px-3 focus:py-2">
        Skip to content
      </a>
      <Header />
      <main id="main" className="mx-auto max-w-[1440px] px-4 pb-20 pt-6 sm:px-6">
        <Outlet />
      </main>
    </div>
  );
}
