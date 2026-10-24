import { NavLink, Outlet, Link } from "react-router";
import { BrandMark } from "../landing/LandingPage";
import { cx } from "../../components/ui";

export default function PublicShell() {
  const nav = ({ isActive }: { isActive: boolean }) =>
    cx("inline-flex h-9 shrink-0 items-center whitespace-nowrap rounded-full px-3 text-[13px] transition sm:px-3.5",
      isActive ? "bg-white/[0.09] text-fog-100" : "text-fog-400 hover:bg-white/[0.05] hover:text-fog-100");
  return (
    <div className="min-h-[100dvh] bg-ink-950 text-fog-100">
      <a href="#main" className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-50 focus:rounded-lg focus:bg-ink-800 focus:px-3 focus:py-2">
        Skip to content
      </a>
      <header className="sticky top-0 z-[1000] border-b border-white/[0.06] bg-ink-950/85 backdrop-blur-xl">
        <div className="mx-auto flex h-16 max-w-[1280px] items-center gap-3 px-4 sm:px-6">
          <Link to="/" aria-label="GridPulse home" className="shrink-0"><BrandMark /></Link>
          <nav aria-label="Public" className="ml-2 flex items-center gap-1 overflow-x-auto [scrollbar-width:none] sm:ml-6">
            <NavLink to="/report" className={nav}>Report a hazard</NavLink>
            <NavLink to="/status" className={nav}>Track &amp; map</NavLink>
          </nav>
          <Link to="/operator/login" className="ml-auto hidden text-[13px] text-fog-400 hover:text-fog-100 sm:inline">
            Operator sign-in
          </Link>
        </div>
      </header>
      <main id="main" className="mx-auto max-w-[1280px] px-4 pb-20 pt-8 sm:px-6">
        <Outlet />
      </main>
      <footer className="border-t border-white/[0.06] py-6 text-center text-xs text-fog-500">
        Map data and tiles © OpenStreetMap contributors (ODbL)
      </footer>
    </div>
  );
}
