import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { createBrowserRouter, RouterProvider, Link, useRouteError } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import "./index.css";
import LandingPage from "./features/landing/LandingPage";
import AppShell from "./app/AppShell";
import { ApiError } from "./services/api";

const queryClient = new QueryClient({
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
        <p className="mt-3 text-sm text-fog-400">This page does not exist in the GridPulse console.</p>
        <Link to="/ops" className="mt-6 inline-block text-sm text-lilac-200 underline underline-offset-4">Back to operations</Link>
      </div>
    </div>
  );
}

const router = createBrowserRouter([
  { path: "/", element: <LandingPage />, errorElement: <RouteError /> },
  {
    path: "/ops",
    element: <AppShell />,
    errorElement: <RouteError />,
    children: [
      { index: true, lazy: () => import("./features/dashboard/DashboardPage").then((m) => ({ Component: m.default })) },
      { path: "city", lazy: () => import("./features/simulation/CityPage").then((m) => ({ Component: m.default })) },
      {
        path: "incidents/:id",
        lazy: () => import("./features/incidents/IncidentLayout").then((m) => ({ Component: m.default })),
        children: [
          { index: true, lazy: () => import("./features/incidents/OverviewTab").then((m) => ({ Component: m.default })) },
          { path: "plans", lazy: () => import("./features/incidents/PlansTab").then((m) => ({ Component: m.default })) },
          { path: "approval", lazy: () => import("./features/incidents/ApprovalTab").then((m) => ({ Component: m.default })) },
          { path: "audit", lazy: () => import("./features/incidents/AuditTab").then((m) => ({ Component: m.default })) },
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
