import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { createBrowserRouter, RouterProvider, Link, useRouteError } from "react-router";
import { MutationCache, QueryCache, QueryClient, QueryClientProvider } from "@tanstack/react-query";
import "./index.css";
import LandingPage from "./features/landing/LandingPage";
import AppShell, { UNAUTHENTICATED_EVENT } from "./app/AppShell";
import PublicShell from "./features/public/PublicShell";
import LoginPage from "./features/auth/LoginPage";
import { ApiError } from "./services/api";

function onError(err: unknown) {
  // An operator request rejected with 401 means the session expired or was revoked.
  if (err instanceof ApiError && err.isUnauthenticated && window.location.pathname.startsWith("/ops")) {
    window.dispatchEvent(new Event(UNAUTHENTICATED_EVENT));
  }
}

const queryClient = new QueryClient({
  queryCache: new QueryCache({ onError }),
  mutationCache: new MutationCache({ onError }),
  defaultOptions: {
    queries: {
      retry: (count, err) => !(err instanceof ApiError && err.status >= 400 && err.status < 500) && count < 2,
      refetchOnWindowFocus: true,
    },
  },
});

function RouteError() {
  const err = useRouteError() as { status?: number } | undefined;
  return (
    <div className="grid min-h-[60dvh] place-items-center px-4 text-center">
      <div>
        <p className="font-display text-5xl font-light text-fog-100">{err?.status === 404 ? "Not found" : "Something broke"}</p>
        <p className="mt-3 text-sm text-fog-400">This page does not exist in GridPulse.</p>
        <Link to="/" className="mt-6 inline-block text-sm text-lilac-200 underline underline-offset-4">Back to the start</Link>
      </div>
    </div>
  );
}

const lazy = (load: () => Promise<{ default: React.ComponentType }>) => () => load().then((m) => ({ Component: m.default }));

const router = createBrowserRouter([
  { path: "/", element: <LandingPage />, errorElement: <RouteError /> },
  {
    element: <PublicShell />,
    errorElement: <RouteError />,
    children: [
      { path: "/report", lazy: lazy(() => import("./features/public/ReportPage")) },
      { path: "/status", lazy: lazy(() => import("./features/public/StatusPage")) },
    ],
  },
  { path: "/operator/login", element: <LoginPage />, errorElement: <RouteError /> },
  {
    path: "/ops",
    element: <AppShell />,
    errorElement: <RouteError />,
    children: [
      { index: true, lazy: lazy(() => import("./features/dashboard/DashboardPage")) },
      { path: "city", lazy: lazy(() => import("./features/simulation/CityPage")) },
      {
        path: "incidents/:id",
        lazy: lazy(() => import("./features/incidents/IncidentLayout")),
        children: [
          { index: true, lazy: lazy(() => import("./features/incidents/OverviewTab")) },
          { path: "plans", lazy: lazy(() => import("./features/incidents/PlansTab")) },
          { path: "approval", lazy: lazy(() => import("./features/incidents/ApprovalTab")) },
          { path: "audit", lazy: lazy(() => import("./features/incidents/AuditTab")) },
        ],
      },
      { path: "*", element: <RouteError /> },
    ],
  },
  { path: "*", element: <RouteError /> },
]);

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  </StrictMode>,
);
