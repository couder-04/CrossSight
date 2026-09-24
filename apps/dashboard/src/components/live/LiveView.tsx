"use client";

import { H3HexagonLayer } from "@deck.gl/geo-layers";
import { PathLayer, ScatterplotLayer } from "@deck.gl/layers";
import { useCallback, useEffect, useMemo, useState } from "react";
import { DeckMap } from "@/components/map/DeckMap";
import { ErrorState } from "@/components/common/ErrorState";
import { LoadingState } from "@/components/common/LoadingState";
import { Badge } from "@/components/ui/Badge";
import { Card, CardHeader } from "@/components/ui/Card";
import { api } from "@/lib/api";
import { LiveSocket } from "@/lib/ws";
import {
  cameraStatusColor,
  congestionColor,
  formatTs,
  h3IntToString,
} from "@/lib/utils";
import type { Alert, Camera, FlowWindow, HeatmapWsPayload, SegmentCongestion } from "@/types";

interface HeatCell {
  h3: string;
  count: number;
}

export function LiveView() {
  const [cameras, setCameras] = useState<Camera[]>([]);
  const [segments, setSegments] = useState<SegmentCongestion[]>([]);
  const [heatmap, setHeatmap] = useState<Map<string, number>>(new Map());
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [flowByCamera, setFlowByCamera] = useState<Map<string, FlowWindow>>(new Map());
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const at = new Date().toISOString();
      const [cams, segs, hm, recentAlerts] = await Promise.all([
        api.cameras(),
        api.segments(at),
        api.heatmap("15m"),
        api.alerts(),
      ]);
      setCameras(cams);
      setSegments(segs.segments);
      const hmMap = new Map<string, number>();
      hm.cells.forEach((c) => hmMap.set(String(c.h3), c.count));
      setHeatmap(hmMap);
      setAlerts(recentAlerts.slice(0, 20));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load live data");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    const socket = new LiveSocket();
    socket.connect();
    socket.subscribe(["heatmap", "alerts", "flow"]);

    const off = socket.onMessage((msg) => {
      if (msg.channel === "heatmap") {
        const data = msg.data as HeatmapWsPayload;
        const h3 = h3IntToString(data.h3_cell ?? data.h3 ?? 0);
        setHeatmap((prev) => {
          const next = new Map(prev);
          next.set(h3, (next.get(h3) ?? 0) + 1);
          return next;
        });
      }
      if (msg.channel === "alerts") {
        const alert = msg.data as Alert;
        setAlerts((prev) => [alert, ...prev].slice(0, 20));
      }
      if (msg.channel === "flow") {
        const fw = msg.data as FlowWindow;
        setFlowByCamera((prev) => new Map(prev).set(fw.camera_id, fw));
      }
    });

    return () => {
      off();
      socket.close();
    };
  }, []);

  const heatData = useMemo<HeatCell[]>(
    () => Array.from(heatmap.entries()).map(([h3, count]) => ({ h3, count })),
    [heatmap],
  );

  const maxHeat = useMemo(
    () => Math.max(1, ...heatData.map((d) => d.count)),
    [heatData],
  );

  const cameraVolumes = useMemo(() => {
    const vols = new Map<string, number>();
    flowByCamera.forEach((fw, id) => vols.set(id, fw.volume ?? 0));
    return vols;
  }, [flowByCamera]);

  const kpis = useMemo(() => {
    const vehicles15m = heatData.reduce((s, c) => s + c.count, 0);
    const speeds = Array.from(flowByCamera.values())
      .map((f) => f.avg_speed_kmh)
      .filter((s): s is number => s != null);
    const avgSpeed = speeds.length
      ? speeds.reduce((a, b) => a + b, 0) / speeds.length
      : null;
    const congested = segments.filter((s) => s.congestion_index >= 0.5).length;
    return { vehicles15m, avgSpeed, congested };
  }, [heatData, flowByCamera, segments]);

  const layers = useMemo(() => {
    const heatLayer = new H3HexagonLayer<HeatCell>({
      id: "heatmap",
      data: heatData,
      pickable: false,
      extruded: true,
      getHexagon: (d) => d.h3,
      getFillColor: (d) => {
        const t = d.count / maxHeat;
        return [56, 189, 248, Math.round(40 + t * 180)] as [number, number, number, number];
      },
      getElevation: (d) => Math.sqrt(d.count) * 80,
      elevationScale: 1,
      coverage: 0.9,
    });

    const segmentPaths = segments
      .map((seg) => {
        const a = cameras.find((c) => c.id === seg.camera_a);
        const b = cameras.find((c) => c.id === seg.camera_b);
        if (!a || !b) return null;
        return {
          path: [[a.lng, a.lat], [b.lng, b.lat]],
          congestion: seg.congestion_index,
          id: `${seg.camera_a}-${seg.camera_b}`,
        };
      })
      .filter(Boolean) as { path: [number, number][]; congestion: number; id: string }[];

    const pathLayer = new PathLayer({
      id: "segments",
      data: segmentPaths,
      getPath: (d) => d.path,
      getColor: (d) => congestionColor(d.congestion),
      getWidth: 4,
      widthMinPixels: 2,
      pickable: true,
    });

    const cameraLayer = new ScatterplotLayer<Camera>({
      id: "cameras",
      data: cameras,
      getPosition: (d) => [d.lng, d.lat],
      getRadius: (d) => 40 + (cameraVolumes.get(d.id) ?? 0) * 2,
      radiusMinPixels: 6,
      radiusMaxPixels: 18,
      getFillColor: (d) => cameraStatusColor(d.status),
      pickable: true,
    });

    return [heatLayer, pathLayer, cameraLayer];
  }, [heatData, maxHeat, segments, cameras, cameraVolumes]);

  if (loading) {
    return <LoadingState label="Loading live map…" />;
  }

  if (error) {
    return <ErrorState message={error} onRetry={load} />;
  }

  return (
    <div className="h-full flex">
      <div className="flex-1 relative">
        <DeckMap layers={layers} />
      </div>

      <aside className="w-80 border-l border-border bg-surface-raised flex flex-col overflow-hidden">
        <div className="p-4 grid grid-cols-1 gap-3 border-b border-border">
          <Kpi label="Vehicles (15m)" value={kpis.vehicles15m.toLocaleString()} />
          <Kpi
            label="Avg speed"
            value={kpis.avgSpeed != null ? `${kpis.avgSpeed.toFixed(1)} km/h` : "—"}
          />
          <Kpi label="Congested segments" value={String(kpis.congested)} tone="warning" />
        </div>

        <Card className="flex-1 m-3 min-h-0 flex flex-col border-0">
          <CardHeader title="Live alerts" />
          <div className="flex-1 overflow-y-auto p-3 space-y-2">
            {alerts.length === 0 && (
              <p className="text-muted text-xs">Waiting for alert stream…</p>
            )}
            {alerts.map((a) => (
              <div
                key={`${a.id ?? a.plate_norm}-${a.ts}`}
                className="rounded border border-border bg-surface-overlay p-2 text-xs"
              >
                <div className="flex items-center justify-between gap-2 mb-1">
                  <Badge tone={a.severity === "high" || a.severity === "critical" ? "danger" : "warning"}>
                    {a.severity}
                  </Badge>
                  <span className="font-mono text-accent">{a.plate_norm}</span>
                </div>
                <p className="text-slate-300">{a.type.replace(/_/g, " ")}</p>
                <p className="text-muted mt-1">{formatTs(a.ts ?? "")}</p>
              </div>
            ))}
          </div>
        </Card>
      </aside>
    </div>
  );
}

function Kpi({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone?: "warning";
}) {
  return (
    <div className="rounded border border-border bg-surface-overlay px-3 py-2">
      <p className="text-[10px] uppercase tracking-wider text-muted">{label}</p>
      <p className={`text-lg font-mono ${tone === "warning" ? "text-warning" : "text-slate-100"}`}>
        {value}
      </p>
    </div>
  );
}
