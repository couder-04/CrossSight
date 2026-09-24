import type {
  Alert,
  AuditEntry,
  Bottleneck,
  Camera,
  FlowWindow,
  GeoJSONFeatureCollection,
  HeatmapResponse,
  LoginResponse,
  ODCell,
  SegmentCongestion,
  UserSession,
  VolumeAnomaly,
  WatchlistEntry,
  Zone,
} from "@/types";

export class ApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

function isServer() {
  return typeof window === "undefined";
}

function apiBase(): string {
  if (isServer()) {
    return process.env.API_INTERNAL_URL ?? process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
  }
  return "/api/backend";
}

async function request<T>(
  path: string,
  options: RequestInit & { token?: string } = {},
): Promise<T> {
  const { token, ...init } = options;
  const headers = new Headers(init.headers);
  if (!headers.has("Content-Type") && init.body && !(init.body instanceof FormData)) {
    headers.set("Content-Type", "application/json");
  }
  if (token) {
    headers.set("Authorization", `Bearer ${token}`);
  }

  const res = await fetch(`${apiBase()}${path}`, { ...init, headers, cache: "no-store" });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail ?? body.error ?? detail;
    } catch {
      // ignore
    }
    throw new ApiError(res.status, String(detail));
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

export const api = {
  login(username: string, password: string): Promise<LoginResponse> {
    const base = process.env.API_INTERNAL_URL ?? process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
    return fetch(`${base}/auth/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password }),
    }).then(async (res) => {
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new ApiError(res.status, body.detail ?? "Login failed");
      }
      return res.json() as Promise<LoginResponse>;
    });
  },

  cameras(token?: string) {
    return request<Camera[]>("/cameras", { token });
  },

  createCamera(data: Partial<Camera> & { id: string; name: string; lat: number; lng: number }, token?: string) {
    return request<Camera>("/cameras", { method: "POST", body: JSON.stringify(data), token });
  },

  deleteCamera(id: string, token?: string) {
    return request<void>(`/cameras/${id}`, { method: "DELETE", token });
  },

  zones(token?: string) {
    return request<Zone[]>("/zones", { token });
  },

  watchlist(token?: string) {
    return request<WatchlistEntry[]>("/watchlist", { token });
  },

  addWatchlist(
    body: { plate_norm: string; reason: string; severity?: string },
    token?: string,
  ) {
    return request<WatchlistEntry>("/watchlist", {
      method: "POST",
      body: JSON.stringify(body),
      token,
    });
  },

  deleteWatchlist(plate: string, token?: string) {
    return request<void>(`/watchlist/${encodeURIComponent(plate)}`, { method: "DELETE", token });
  },

  importWatchlistCsv(file: File, token?: string) {
    const form = new FormData();
    form.append("file", file);
    return request<{ imported: number }>("/watchlist/import", {
      method: "POST",
      body: form,
      token,
    });
  },

  audit(token?: string, limit = 100) {
    return request<AuditEntry[]>(`/audit?limit=${limit}`, { token });
  },

  alerts(params: Record<string, string> = {}, token?: string) {
    const qs = new URLSearchParams(params).toString();
    return request<Alert[]>(`/alerts${qs ? `?${qs}` : ""}`, { token });
  },

  alert(id: string, token?: string) {
    return request<Alert>(`/alerts/${id}`, { token });
  },

  ackAlert(id: string, token?: string) {
    return request<Alert>(`/alerts/${id}/ack`, { method: "POST", body: "{}", token });
  },

  dispatchAlert(id: string, dispatched_to: string, token?: string) {
    return request<Alert>(`/alerts/${id}/dispatch`, {
      method: "POST",
      body: JSON.stringify({ dispatched_to }),
      token,
    });
  },

  closeAlert(id: string, note: string, token?: string) {
    return request<Alert>(`/alerts/${id}/close`, {
      method: "POST",
      body: JSON.stringify({ note }),
      token,
    });
  },

  trajectory(params: {
    plate: string;
    case_id: string;
    from?: string;
    to?: string;
    fuzzy?: boolean;
  }, token?: string) {
    const qs = new URLSearchParams();
    qs.set("plate", params.plate);
    qs.set("case_id", params.case_id);
    if (params.from) qs.set("from", params.from);
    if (params.to) qs.set("to", params.to);
    if (params.fuzzy) qs.set("fuzzy", "true");
    return request<GeoJSONFeatureCollection>(`/trajectory?${qs}`, { token });
  },

  heatmap(window = "15m", token?: string) {
    return request<HeatmapResponse>(`/analytics/heatmap?window=${encodeURIComponent(window)}`, { token });
  },

  flow(camera_id: string, from: string, to: string, token?: string) {
    const qs = new URLSearchParams({ camera_id, from, to });
    return request<{ windows: FlowWindow[] }>(`/analytics/flow?${qs}`, { token });
  },

  segments(at: string, token?: string) {
    return request<{ segments: SegmentCongestion[] }>(
      `/analytics/segments?at=${encodeURIComponent(at)}`,
      { token },
    );
  },

  od(hour: number, date: string, token?: string) {
    const qs = new URLSearchParams({ hour: String(hour), date });
    return request<{ cells: ODCell[] }>(`/analytics/od?${qs}`, { token });
  },

  bottlenecks(token?: string) {
    return request<{ bottlenecks: Bottleneck[] }>("/analytics/bottlenecks", { token });
  },

  anomalies(token?: string) {
    return request<{ anomalies: VolumeAnomaly[] }>("/analytics/anomalies", { token });
  },
};

export type { UserSession };
