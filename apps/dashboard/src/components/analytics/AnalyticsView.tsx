"use client";

import { ArcLayer } from "@deck.gl/layers";
import { cellToLatLng } from "h3-js";
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { DeckMap, flyTo } from "@/components/map/DeckMap";
import { ErrorState } from "@/components/common/ErrorState";
import { LoadingState } from "@/components/common/LoadingState";
import { Button } from "@/components/ui/Button";
import { Card, CardHeader } from "@/components/ui/Card";
import { Select } from "@/components/ui/Select";
import { api } from "@/lib/api";
import type { Bottleneck, Camera, ODCell, VolumeAnomaly } from "@/types";
import type maplibregl from "maplibre-gl";

interface ArcDatum {
  source: [number, number];
  target: [number, number];
  trips: number;
}

export function AnalyticsView() {
  const [cameras, setCameras] = useState<Camera[]>([]);
  const [hour, setHour] = useState(8);
  // Prefer yesterday so backfill OD cells are visible by default.
  const [date, setDate] = useState(() => {
    const d = new Date();
    d.setDate(d.getDate() - 1);
    return d.toISOString().slice(0, 10);
  });
  const [odCells, setOdCells] = useState<ODCell[]>([]);
  const [bottlenecks, setBottlenecks] = useState<Bottleneck[]>([]);
  const [anomalies, setAnomalies] = useState<VolumeAnomaly[]>([]);
  const [selectedCamera, setSelectedCamera] = useState("");
  const [flowData, setFlowData] = useState<{ time: string; volume: number; speed: number | null }[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [mapRef, setMapRef] = useState<maplibregl.Map | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [cams, od, bn, an] = await Promise.all([
        api.cameras(),
        api.od(hour, date),
        api.bottlenecks(),
        api.anomalies(),
      ]);
      setCameras(cams);
      setOdCells(od.cells);
      setBottlenecks(bn.bottlenecks);
      setAnomalies(an.anomalies);
      if (!selectedCamera && cams[0]) setSelectedCamera(cams[0].id);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load analytics");
    } finally {
      setLoading(false);
    }
  }, [hour, date]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    if (!selectedCamera) return;
    const to = new Date();
    const from = new Date(to.getTime() - 6 * 3600 * 1000);
    api
      .flow(selectedCamera, from.toISOString(), to.toISOString())
      .then((res) => {
        setFlowData(
          res.windows.map((w) => ({
            time: new Date(w.window_start).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
            volume: w.volume ?? 0,
            speed: w.avg_speed_kmh ?? null,
          })),
        );
      })
      .catch(() => setFlowData([]));
  }, [selectedCamera]);

  const arcs = useMemo<ArcDatum[]>(() => {
    const maxTrips = Math.max(1, ...odCells.map((c) => c.trip_count));
    return odCells.map((c) => {
      const [oLat, oLng] = cellToLatLng(String(c.origin_h3));
      const [dLat, dLng] = cellToLatLng(String(c.dest_h3));
      return {
        source: [oLng, oLat],
        target: [dLng, dLat],
        trips: c.trip_count / maxTrips,
      };
    });
  }, [odCells]);

  const layers = useMemo(
    () => [
      new ArcLayer<ArcDatum>({
        id: "od-arcs",
        data: arcs,
        getSourcePosition: (d) => d.source,
        getTargetPosition: (d) => d.target,
        getSourceColor: [56, 189, 248, 180],
        getTargetColor: [167, 139, 250, 180],
        getWidth: (d) => 1 + d.trips * 8,
        pickable: true,
      }),
    ],
    [arcs],
  );

  function zoomToBottleneck(b: Bottleneck) {
    const cam = cameras.find((c) => c.id === b.camera_a);
    if (cam && mapRef) flyTo(mapRef, cam.lng, cam.lat, 14);
  }

  if (loading) return <LoadingState label="Loading analytics…" />;
  if (error) return <ErrorState message={error} onRetry={load} />;

  return (
    <div className="h-full grid grid-rows-[1fr_auto] lg:grid-rows-1 lg:grid-cols-[1fr_22rem]">
      <div className="relative min-h-[280px]">
        <DeckMap layers={layers} onMapReady={setMapRef} />
        <div className="absolute top-3 left-3 bg-surface-raised/95 border border-border rounded-lg p-3 flex gap-3 text-sm">
          <label className="flex flex-col gap-1">
            <span className="text-muted text-xs">Hour</span>
            <input
              type="range"
              min={0}
              max={23}
              value={hour}
              onChange={(e) => setHour(Number(e.target.value))}
              className="w-32"
            />
            <span className="font-mono text-xs">{hour}:00</span>
          </label>
          <InputDate value={date} onChange={setDate} />
          <Button size="sm" variant="secondary" onClick={load}>Refresh OD</Button>
        </div>
      </div>

      <aside className="border-t lg:border-t-0 lg:border-l border-border bg-surface-raised overflow-y-auto p-3 space-y-3">
        <Card>
          <CardHeader title="Camera flow" />
          <div className="p-3 space-y-2">
            <Select
              label="Camera"
              value={selectedCamera}
              onChange={(e) => setSelectedCamera(e.target.value)}
            >
              {cameras.map((c) => (
                <option key={c.id} value={c.id}>{c.name}</option>
              ))}
            </Select>
            <div className="h-44">
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={flowData}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#334155" />
                  <XAxis dataKey="time" tick={{ fill: "#94a3b8", fontSize: 10 }} />
                  <YAxis yAxisId="vol" tick={{ fill: "#94a3b8", fontSize: 10 }} />
                  <YAxis yAxisId="spd" orientation="right" tick={{ fill: "#94a3b8", fontSize: 10 }} />
                  <Tooltip contentStyle={{ background: "#1e293b", border: "1px solid #334155" }} />
                  <Legend />
                  <Line yAxisId="vol" type="monotone" dataKey="volume" stroke="#38bdf8" dot={false} />
                  <Line yAxisId="spd" type="monotone" dataKey="speed" stroke="#a78bfa" dot={false} />
                </LineChart>
              </ResponsiveContainer>
            </div>
          </div>
        </Card>

        <Card>
          <CardHeader title="Bottlenecks" />
          <div className="max-h-40 overflow-y-auto">
            <table>
              <tbody>
                {bottlenecks.map((b) => (
                  <tr
                    key={`${b.camera_a}-${b.camera_b}`}
                    className="cursor-pointer hover:bg-surface-overlay"
                    onClick={() => zoomToBottleneck(b)}
                  >
                    <td className="font-mono text-xs">{b.camera_a}→{b.camera_b}</td>
                    <td className="text-warning text-xs text-right">{b.congestion_index.toFixed(2)}</td>
                  </tr>
                ))}
                {bottlenecks.length === 0 && (
                  <tr><td className="text-muted text-xs p-3">No bottlenecks</td></tr>
                )}
              </tbody>
            </table>
          </div>
        </Card>

        <Card>
          <CardHeader title="Volume anomalies" />
          <div className="max-h-40 overflow-y-auto">
            <table>
              <tbody>
                {anomalies.map((a) => (
                  <tr key={a.camera_id}>
                    <td className="font-mono text-xs">{a.camera_id}</td>
                    <td className="text-danger text-xs text-right">z={a.z_score}</td>
                  </tr>
                ))}
                {anomalies.length === 0 && (
                  <tr><td className="text-muted text-xs p-3">No anomalies</td></tr>
                )}
              </tbody>
            </table>
          </div>
        </Card>
      </aside>
    </div>
  );
}

function InputDate({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  return (
    <label className="flex flex-col gap-1 text-sm">
      <span className="text-muted text-xs">Date</span>
      <input type="date" value={value} onChange={(e) => onChange(e.target.value)} />
    </label>
  );
}
