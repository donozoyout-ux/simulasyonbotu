import type { DataHealth } from "@/types";

export type DashboardSystemMode =
  | "LIVE PAPER" | "DATA DEGRADED" | "DATA ERROR"
  | "API DEGRADED" | "BACKEND OFFLINE" | "MOCK DATA";

export type IsolatedResult<T> = { ok: true; value: T } | { ok: false; label: string };

export function preserveSuccessfulState<T>(current: T, result: IsolatedResult<T>): T {
  return result.ok ? result.value : current;
}

export function deriveDashboardStatus(input: {
  backendHealthOk: boolean;
  failedModules: string[];
  dataHealth?: DataHealth;
}): DashboardSystemMode {
  if (!input.backendHealthOk) return "BACKEND OFFLINE";
  if (input.dataHealth?.mode === "MOCK") return "MOCK DATA";
  if (input.dataHealth?.system_status === "DATA_ERROR" ||
      input.dataHealth?.severity === "CRITICAL") return "DATA ERROR";
  if (input.dataHealth?.system_status === "DATA_DEGRADED" ||
      input.dataHealth?.severity === "WARNING" || input.dataHealth?.status === "PARTIAL") {
    return "DATA DEGRADED";
  }
  if (input.failedModules.length) return "API DEGRADED";
  return "LIVE PAPER";
}
