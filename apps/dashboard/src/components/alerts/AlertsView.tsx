"use client";

import { ScatterplotLayer } from "@deck.gl/layers";
import { useCallback, useEffect, useMemo, useState } from "react";
import { DeckMap } from "@/components/map/DeckMap";
import { ErrorState } from "@/components/common/ErrorState";
import { LoadingState } from "@/components/common/LoadingState";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Select } from "@/components/ui/Select";
import { api } from "@/lib/api";
import { LiveSocket } from "@/lib/ws";
import { formatTs } from "@/lib/utils";
import type { Alert, Camera } from "@/types";

export function AlertsView() {
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [cameras, setCameras] = useState<Camera[]>([]);
  const [selected, setSelected] = useState<Alert | null>(null);
  const [status, setStatus] = useState("");
  const [type, setType] = useState("");
  const [severity, setSeverity] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [closeNote, setCloseNote] = useState("");
  const [dispatchTo, setDispatchTo] = useState("");
  const [actionLoading, setActionLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const params: Record<string, string> = {};
      if (status) params.status = status;
      if (type) params.type = type;
      if (severity) params.severity = severity;
      const [list, cams] = await Promise.all([api.alerts(params), api.cameras()]);
      setAlerts(list);
      setCameras(cams);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load alerts");
    } finally {
      setLoading(false);
    }
  }, [status, type, severity]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    const socket = new LiveSocket();
    socket.connect();
    socket.subscribe(["alerts"]);
    const off = socket.onMessage((msg) => {
      if (msg.channel === "alerts") {
        const alert = msg.data as Alert;
        setAlerts((prev) => {
          const exists = prev.find((a) => a.id === alert.id);
          if (exists) return prev.map((a) => (a.id === alert.id ? { ...a, ...alert } : a));
          return [alert, ...prev];
        });
      }
    });
    return () => {
      off();
      socket.close();
    };
  }, []);

  async function openDetail(id: string) {
    try {
      const detail = await api.alert(id);
      setSelected(detail);
      setCloseNote("");
      setDispatchTo("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load alert");
    }
  }

  async function runAction(action: "ack" | "dispatch" | "close") {
    if (!selected?.id) return;
    const alertId = selected.id;
    setActionLoading(true);
    try {
      let updated: Alert;
      if (action === "ack") updated = await api.ackAlert(alertId);
      else if (action === "dispatch") updated = await api.dispatchAlert(alertId, dispatchTo);
      else updated = await api.closeAlert(alertId, closeNote);
      setSelected(updated);
      setAlerts((prev) => prev.map((a) => (a.id === updated.id ? updated : a)));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Action failed");
    } finally {
      setActionLoading(false);
    }
  }

  const evidencePoints = useMemo(() => {
    if (!selected) return [];
    const evidence = selected.evidence as { reads?: Array<{ camera_id?: string }> } | undefined;
    const reads = evidence?.reads ?? [];
    const ids = new Set([
      ...(selected.camera_ids ?? []),
      ...reads.map((r) => r.camera_id).filter(Boolean) as string[],
    ]);
    return cameras.filter((c) => ids.has(c.id));
  }, [selected, cameras]);

  const miniMapLayers = useMemo(
    () => [
      new ScatterplotLayer<Camera>({
        id: "evidence-cams",
        data: evidencePoints,
        getPosition: (d) => [d.lng, d.lat],
        getRadius: 80,
        radiusMinPixels: 8,
        getFillColor: [239, 68, 68, 220],
      }),
    ],
    [evidencePoints],
  );

  if (loading) return <LoadingState label="Loading alerts…" />;
  if (error && alerts.length === 0) return <ErrorState message={error} onRetry={load} />;

  return (
    <div className="h-full flex">
      <div className="flex-1 flex flex-col min-w-0">
        <div className="shrink-0 border-b border-border bg-surface-raised px-4 py-3 flex flex-wrap gap-3">
          <Select label="Status" value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="">All</option>
            <option value="new">New</option>
            <option value="acknowledged">Acknowledged</option>
            <option value="dispatched">Dispatched</option>
            <option value="closed">Closed</option>
          </Select>
          <Select label="Type" value={type} onChange={(e) => setType(e.target.value)}>
            <option value="">All</option>
            <option value="watchlist">Watchlist</option>
            <option value="cloned_plate">Cloned plate</option>
            <option value="convoy">Convoy</option>
            <option value="loitering">Loitering</option>
            <option value="geofence">Geofence</option>
            <option value="wrong_way">Wrong way</option>
          </Select>
          <Select label="Severity" value={severity} onChange={(e) => setSeverity(e.target.value)}>
            <option value="">All</option>
            <option value="low">Low</option>
            <option value="medium">Medium</option>
            <option value="high">High</option>
            <option value="critical">Critical</option>
          </Select>
          <Button variant="secondary" size="sm" onClick={load}>Apply</Button>
        </div>

        <div className="flex-1 overflow-auto">
          <table>
            <thead>
              <tr>
                <th>Time</th>
                <th>Plate</th>
                <th>Type</th>
                <th>Severity</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {alerts.map((a) => (
                <tr
                  key={a.id ?? a.plate_norm}
                  className="cursor-pointer hover:bg-surface-overlay"
                  onClick={() => a.id && openDetail(a.id)}
                >
                  <td className="text-xs text-muted">{formatTs(a.ts ?? "")}</td>
                  <td className="font-mono">{a.plate_norm}</td>
                  <td>{a.type.replace(/_/g, " ")}</td>
                  <td><Badge tone={a.severity === "high" ? "danger" : "warning"}>{a.severity}</Badge></td>
                  <td>{a.status ?? "new"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {selected && (
        <aside className="w-[28rem] border-l border-border bg-surface-raised flex flex-col overflow-hidden">
          <div className="p-4 border-b border-border flex items-start justify-between gap-2">
            <div>
              <p className="font-mono text-lg text-accent">{selected.plate_norm}</p>
              <p className="text-sm text-muted">{selected.type.replace(/_/g, " ")}</p>
            </div>
            <Button variant="ghost" size="sm" onClick={() => setSelected(null)}>✕</Button>
          </div>

          {selected.needs_verification && (
            <div className="mx-4 mt-3 rounded border border-warning/40 bg-warning/10 px-3 py-2 text-sm text-warning">
              Fuzzy match — needs verification before dispatch.
            </div>
          )}

          <div className="h-40 m-4 rounded border border-border overflow-hidden">
            <DeckMap layers={miniMapLayers} />
          </div>

          <div className="px-4 text-xs space-y-2 overflow-y-auto flex-1">
            <p className="text-muted">Cameras: {(selected.camera_ids ?? []).join(", ") || "—"}</p>
            <pre className="bg-surface-overlay rounded p-2 overflow-x-auto text-[10px] font-mono">
              {JSON.stringify(selected.evidence, null, 2)}
            </pre>
            {(selected as Alert & { crop_url?: string }).crop_url && (
              <img src={(selected as Alert & { crop_url?: string }).crop_url} alt="Plate crop" className="rounded border border-border max-h-32" />
            )}
          </div>

          <div className="p-4 border-t border-border space-y-2">
            <div className="flex gap-2">
              <Button size="sm" disabled={actionLoading} onClick={() => runAction("ack")}>Acknowledge</Button>
            </div>
            <Input
              label="Dispatch to"
              value={dispatchTo}
              onChange={(e) => setDispatchTo(e.target.value)}
              placeholder="unit-id"
            />
            <Button size="sm" variant="secondary" disabled={actionLoading || !dispatchTo} onClick={() => runAction("dispatch")}>
              Dispatch
            </Button>
            <Input
              label="Close note (required)"
              value={closeNote}
              onChange={(e) => setCloseNote(e.target.value)}
            />
            <Button size="sm" variant="danger" disabled={actionLoading || !closeNote.trim()} onClick={() => runAction("close")}>
              Close
            </Button>
          </div>
        </aside>
      )}
    </div>
  );
}
