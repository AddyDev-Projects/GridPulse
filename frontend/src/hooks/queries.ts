import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, authApi, publicApi, type DecisionBody } from "../services/api";
import type { PublicReportInput } from "../types/api";

export const keys = {
  me: ["me"] as const,
  city: ["public-city"] as const,
  publicIncidents: ["public-incidents"] as const,
  publicClosures: ["public-closures"] as const,
  reportStatus: (ref: string) => ["report-status", ref] as const,
  state: ["state"] as const,
  overlay: ["overlay"] as const,
  scenarios: ["scenarios"] as const,
  incidents: (scope: string) => ["incidents", scope] as const,
  incident: (id: string) => ["incident", id] as const,
  workflow: (id: string) => ["workflow", id] as const,
  audit: (id: string) => ["audit", id] as const,
  recentAudit: ["recent-audit"] as const,
};

// ---- public --------------------------------------------------------------------------------------
export const useCity = () => useQuery({ queryKey: keys.city, queryFn: publicApi.city, staleTime: Infinity });
export const usePublicIncidents = () =>
  useQuery({ queryKey: keys.publicIncidents, queryFn: publicApi.incidents, refetchInterval: 30_000 });
export const usePublicClosures = () =>
  useQuery({ queryKey: keys.publicClosures, queryFn: publicApi.closures, refetchInterval: 30_000 });
export const useReportStatus = (ref: string | null) =>
  useQuery({ queryKey: keys.reportStatus(ref ?? ""), queryFn: () => publicApi.reportStatus(ref!), enabled: !!ref });

export function useSubmitReport() {
  return useMutation({ mutationFn: (data: PublicReportInput) => publicApi.submitReport(data) });
}

// ---- auth ----------------------------------------------------------------------------------------
export function useMe() {
  return useQuery({ queryKey: keys.me, queryFn: authApi.me, retry: false, staleTime: 60_000 });
}

export function useLogin() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ username, password }: { username: string; password: string }) => authApi.login(username, password),
    onSuccess: (res) => {
      qc.setQueryData(keys.me, res.operator);
    },
  });
}

export function useLogout() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: authApi.logout,
    onSettled: () => {
      qc.removeQueries({ predicate: (q) => q.queryKey[0] !== "public-city" });
    },
  });
}

// ---- operator ------------------------------------------------------------------------------------
export const useSimState = () => useQuery({ queryKey: keys.state, queryFn: api.state, refetchInterval: 10_000 });
export const useOverlay = () => useQuery({ queryKey: keys.overlay, queryFn: api.overlay, refetchInterval: 15_000 });
export const useScenarios = () => useQuery({ queryKey: keys.scenarios, queryFn: api.scenarios, staleTime: Infinity });
export const useIncidents = (scope: "current" | "all" = "current") =>
  useQuery({ queryKey: keys.incidents(scope), queryFn: () => api.incidents(scope), refetchInterval: 5_000 });
export const useRecentAudit = () =>
  useQuery({ queryKey: keys.recentAudit, queryFn: () => api.recentAudit(10), refetchInterval: 5_000 });

export function useWorkflow(id: string) {
  return useQuery({
    queryKey: keys.workflow(id),
    queryFn: () => api.workflow(id),
    // Poll quickly only while the coordinator is actually running on the server.
    refetchInterval: (q) => (q.state.data?.running ? 700 : false),
  });
}

export function useIncident(id: string, running: boolean) {
  return useQuery({ queryKey: keys.incident(id), queryFn: () => api.incident(id), refetchInterval: running ? 900 : false });
}

export function useIncidentAudit(id: string, running: boolean) {
  return useQuery({ queryKey: keys.audit(id), queryFn: () => api.incidentAudit(id), refetchInterval: running ? 900 : false });
}

/** After any mutation, refetch everything derived from server state. Nothing is updated optimistically. */
function useInvalidateAll() {
  const qc = useQueryClient();
  return () => qc.invalidateQueries({ predicate: (q) => !["scenarios", "public-city", "me"].includes(String(q.queryKey[0])) });
}

export function useLoadScenario() {
  const invalidate = useInvalidateAll();
  return useMutation({ mutationFn: (key: string) => api.loadScenario(key, true), onSettled: invalidate });
}

export function useClearSimulation() {
  const invalidate = useInvalidateAll();
  return useMutation({ mutationFn: api.clearSimulation, onSettled: invalidate });
}

export function useIncidentAction() {
  const invalidate = useInvalidateAll();
  return useMutation({
    mutationFn: (a: { kind: "run" | "verify" | "dismiss"; id: string; text?: string }) =>
      a.kind === "run" ? api.runIncident(a.id) : a.kind === "verify" ? api.verify(a.id, a.text) : api.dismiss(a.id, a.text ?? ""),
    onSettled: invalidate,
  });
}

export function useDecision(kind: "approve" | "reject") {
  const invalidate = useInvalidateAll();
  return useMutation({
    mutationFn: ({ planId, body }: { planId: string; body: DecisionBody }) =>
      kind === "approve" ? api.approve(planId, body) : api.reject(planId, body),
    onSettled: invalidate,
  });
}

export function useRevalidate() {
  const invalidate = useInvalidateAll();
  return useMutation({ mutationFn: (planId: string) => api.revalidate(planId), onSettled: invalidate });
}

export function useExecute() {
  const invalidate = useInvalidateAll();
  return useMutation({ mutationFn: (planId: string) => api.execute(planId), onSettled: invalidate });
}
