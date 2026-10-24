import type {
  AuditEvent,
  City,
  Closure,
  Execution,
  Incident,
  IncidentDetail,
  IncidentSummary,
  LoginResponse,
  OperatorInfo,
  Overlay,
  Plan,
  PublicIncident,
  PublicRecentIncident,
  PublicReportInput,
  PublicReportReceipt,
  PublicReportStatus,
  RoadSegment,
  Scenario,
  SimulationState,
  Workflow,
} from "../types/api";

export class ApiError extends Error {
  status: number;
  code: string;
  retryAfter: number | null;
  constructor(status: number, code: string, message: string, retryAfter: number | null = null) {
    super(message);
    this.status = status;
    this.code = code;
    this.retryAfter = retryAfter;
  }
  get isUnauthenticated() {
    return this.status === 401;
  }
}

/** The CSRF token issued at login lives in a readable (non-HttpOnly) cookie; the session cookie does not. */
export function csrfToken(): string | null {
  const m = document.cookie.split("; ").find((c) => c.startsWith("gp_csrf="));
  return m ? decodeURIComponent(m.slice("gp_csrf=".length)) : null;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const method = (init?.method ?? "GET").toUpperCase();
  const headers: Record<string, string> = { "Content-Type": "application/json", ...(init?.headers as Record<string, string>) };
  if (method !== "GET") {
    const csrf = csrfToken();
    if (csrf) headers["X-CSRF-Token"] = csrf;
  }
  let res: Response;
  try {
    res = await fetch(`/api${path}`, { ...init, method, headers, credentials: "same-origin" });
  } catch {
    throw new ApiError(0, "network_error", "Cannot reach the GridPulse server. Check your connection and try again.");
  }
  if (res.status === 204) return undefined as T;
  const body = await res.json().catch(() => null);
  if (!res.ok) {
    const detail = body?.detail;
    const retry = res.headers.get("Retry-After");
    if (detail && typeof detail === "object" && "message" in detail) {
      const errors = Array.isArray(detail.errors)
        ? " " + detail.errors.map((e: { field: string; message: string }) => `${e.field}: ${e.message}`).join("; ")
        : "";
      throw new ApiError(res.status, detail.code ?? "error", `${detail.message}${errors}`, retry ? Number(retry) : null);
    }
    throw new ApiError(res.status, "error", `Request failed (${res.status}).`);
  }
  return body as T;
}

const post = <T>(path: string, data?: unknown) =>
  request<T>(path, { method: "POST", body: data === undefined ? undefined : JSON.stringify(data) });

export interface DecisionBody {
  plan_version: number;
  content_hash: string;
  note?: string;
}

/** Endpoints anyone can use without signing in. */
export const publicApi = {
  city: () => request<City>("/public/city"),
  submitReport: (data: PublicReportInput) => post<PublicReportReceipt>("/public/reports", data),
  reportStatus: (reference: string) => request<PublicReportStatus>(`/public/reports/${encodeURIComponent(reference)}`),
  incidents: () => request<PublicIncident[]>("/public/incidents"),
  recentIncidents: () => request<PublicRecentIncident[]>("/public/recent-incidents"),
  closures: () => request<Closure[]>("/public/closures"),
  segments: (s: number, w: number, n: number, e: number) =>
    request<{ segments: RoadSegment[]; truncated: boolean }>(
      `/public/map/segments?south=${s}&west=${w}&north=${n}&east=${e}`,
    ),
};

export const authApi = {
  login: (username: string, password: string) => post<LoginResponse>("/auth/login", { username, password }),
  logout: () => post<void>("/auth/logout"),
  me: () => request<OperatorInfo>("/auth/me"),
};

/** Operator endpoints. The backend rejects every one of these without a valid session. */
export const api = {
  scenarios: () => request<Scenario[]>("/scenarios"),
  state: () => request<SimulationState>("/simulation/state"),
  overlay: () => request<Overlay>("/simulation/overlay"),
  loadScenario: (key: string, createReport = true) =>
    post<SimulationState>(`/simulation/scenarios/${key}/load`, { create_report: createReport }),
  clearSimulation: () => post<SimulationState>("/simulation/clear"),
  incidents: (scope: "current" | "all" = "current") => request<IncidentSummary[]>(`/incidents?scope=${scope}`),
  incident: (id: string) => request<IncidentDetail>(`/incidents/${id}`),
  verify: (id: string, note?: string) => post<Incident>(`/incidents/${id}/verify`, { note: note || null }),
  dismiss: (id: string, reason: string) => post<Incident>(`/incidents/${id}/dismiss`, { reason }),
  runIncident: (id: string) => post<Incident>(`/incidents/${id}/run`),
  workflow: (id: string) => request<Workflow>(`/incidents/${id}/workflow`),
  incidentAudit: (id: string) => request<AuditEvent[]>(`/incidents/${id}/audit`),
  recentAudit: (limit = 12) => request<AuditEvent[]>(`/audit?limit=${limit}`),
  plan: (id: string) => request<Plan>(`/plans/${id}`),
  approve: (id: string, body: DecisionBody) => post<Plan>(`/plans/${id}/approve`, body),
  reject: (id: string, body: DecisionBody) => post<Plan>(`/plans/${id}/reject`, body),
  revalidate: (id: string) => post<Plan>(`/plans/${id}/revalidate`),
  execute: (id: string) => post<Execution>(`/plans/${id}/execute`),
};
