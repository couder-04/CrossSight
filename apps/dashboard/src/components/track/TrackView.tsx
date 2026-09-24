"use client";

import { PathStyleExtension } from "@deck.gl/extensions";
import { TripsLayer } from "@deck.gl/geo-layers";
import { PathLayer, ScatterplotLayer } from "@deck.gl/layers";
import { useCallback, useMemo, useState } from "react";
import { DeckMap } from "@/components/map/DeckMap";
import { ErrorState } from "@/components/common/ErrorState";
import { LoadingState } from "@/components/common/LoadingState";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Badge } from "@/components/ui/Badge";
import { api } from "@/lib/api";
import { formatConfidence, formatTs } from "@/lib/utils";
import type { GeoJSONFeatureCollection } from "@/types";

interface Sighting {
  ts: string;
  camera_id: string;
  direction?: string;
  confidence: number;
  crop_key?: string;
  coordinates: [number, number];
}

interface TripPath {
  path: [number, number][];
  timestamps: number[];
  observed: boolean;
  feasible: boolean;
  impossible_hop: boolean;
  from_camera: string;
  to_camera: string;
}

export function TrackView() {
  const defaultFrom = () => {
    const d = new Date();
    d.setDate(d.getDate() - 7);
    return d.toISOString().slice(0, 16);
  };
  const defaultTo = () => new Date(Date.now() + 86400000).toISOString().slice(0, 16);

  const [plate, setPlate] = useState("");
  const [caseId, setCaseId] = useState("");
  const [from, setFrom] = useState(defaultFrom);
  const [to, setTo] = useState(defaultTo);
  const [fuzzy, setFuzzy] = useState(false);
  const [data, setData] = useState<GeoJSONFeatureCollection | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [playing, setPlaying] = useState(false);
  const [timeFrac, setTimeFrac] = useState(1);

  const search = useCallback(async () => {
    if (!plate.trim() || !caseId.trim()) {
      setError("Plate and case ID are required");
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const result = await api.trajectory({
        plate: plate.trim(),
        case_id: caseId.trim(),
        from: from || undefined,
        to: to || undefined,
        fuzzy,
      });
      setData(result);
      setTimeFrac(1);
      setPlaying(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Trajectory query failed");
      setData(null);
    } finally {
      setLoading(false);
    }
  }, [plate, caseId, from, to, fuzzy]);

  const { sightings, trips, timeRange } = useMemo(() => {
    if (!data) return { sightings: [] as Sighting[], trips: [] as TripPath[], timeRange: [0, 1] as [number, number] };

    const sightingList: Sighting[] = [];
    const tripList: TripPath[] = [];

    for (const f of data.features) {
      const props = f.properties ?? {};
      const geom = f.geometry;
      if (geom.type === "Point" && props.kind === "sighting") {
        const coords = geom.coordinates as [number, number];
        sightingList.push({
          ts: String(props.ts),
          camera_id: String(props.camera_id),
          direction: props.direction ? String(props.direction) : undefined,
          confidence: Number(props.confidence ?? 0),
          crop_key: props.crop_key ? String(props.crop_key) : undefined,
          coordinates: coords,
        });
      }
      if (geom.type === "LineString" && props.kind === "leg") {
        const coords = geom.coordinates as [number, number][];
        const ts0 = sightingList.find((s) => s.camera_id === props.from_camera)?.ts;
        const ts1 = sightingList.find((s) => s.camera_id === props.to_camera)?.ts;
        const t0 = ts0 ? new Date(ts0).getTime() : Date.now();
        const t1 = ts1 ? new Date(ts1).getTime() : t0 + 60000;
        tripList.push({
          path: coords,
          timestamps: [t0, t1],
          observed: Boolean(props.observed),
          feasible: Boolean(props.feasible),
          impossible_hop: Boolean(props.impossible_hop),
          from_camera: String(props.from_camera),
          to_camera: String(props.to_camera),
        });
      }
    }

    sightingList.sort((a, b) => new Date(a.ts).getTime() - new Date(b.ts).getTime());
    const times = sightingList.map((s) => new Date(s.ts).getTime());
    const min = times[0] ?? 0;
    const max = times[times.length - 1] ?? min + 1;

    return { sightings: sightingList, trips: tripList, timeRange: [min, max] as [number, number] };
  }, [data]);

  const currentTime = timeRange[0] + (timeRange[1] - timeRange[0]) * timeFrac;

  const layers = useMemo(() => {
    if (!data) return [];

    const dashExt = new PathStyleExtension({ dash: true });
    const observedTrips = trips.filter((t) => t.observed);

    const tripLayer = new TripsLayer<TripPath>({
      id: "trips",
      data: observedTrips,
      getPath: (d) => d.path,
      getTimestamps: (d) => d.timestamps,
      getColor: (d) =>
        d.impossible_hop ? [239, 68, 68, 230] : [56, 189, 248, 220],
      getWidth: 5,
      trailLength: 600000,
      currentTime: playing ? currentTime : timeRange[1],
      opacity: 0.9,
    });

    const inferredLayer = new PathLayer<TripPath>({
      id: "inferred-legs",
      data: trips.filter((t) => !t.observed),
      getPath: (d) => d.path,
      getColor: (d) =>
        d.impossible_hop ? [239, 68, 68, 230] : [148, 163, 184, 200],
      getWidth: 4,
      extensions: [dashExt],
      // PathStyleExtension props (not in PathLayer TS types)
      getDashArray: [4, 4] as [number, number],
      dashJustified: true,
    } as ConstructorParameters<typeof PathLayer<TripPath>>[0]);

    const points = new ScatterplotLayer<Sighting>({
      id: "sightings",
      data: sightings.filter((s) => new Date(s.ts).getTime() <= currentTime),
      getPosition: (d) => d.coordinates,
      getRadius: 60,
      radiusMinPixels: 5,
      getFillColor: [34, 197, 94, 220],
      pickable: true,
    });

    return [inferredLayer, tripLayer, points];
  }, [data, trips, sightings, playing, currentTime, timeRange]);

  return (
    <div className="h-full flex flex-col">
      <form
        className="shrink-0 border-b border-border bg-surface-raised px-4 py-3 grid grid-cols-2 md:grid-cols-6 gap-3 items-end"
        onSubmit={(e) => {
          e.preventDefault();
          search();
        }}
      >
        <Input label="Plate" value={plate} onChange={(e) => setPlate(e.target.value)} placeholder="BR01AB1234" required />
        <Input label="Case ID" value={caseId} onChange={(e) => setCaseId(e.target.value)} required />
        <Input label="From" type="datetime-local" value={from} onChange={(e) => setFrom(e.target.value)} />
        <Input label="To" type="datetime-local" value={to} onChange={(e) => setTo(e.target.value)} />
        <label className="flex items-center gap-2 text-sm text-muted pb-2">
          <input type="checkbox" checked={fuzzy} onChange={(e) => setFuzzy(e.target.checked)} />
          Fuzzy match
        </label>
        <Button type="submit" disabled={loading}>Search</Button>
      </form>

      {loading && <LoadingState label="Reconstructing trajectory…" />}
      {error && !loading && <ErrorState message={error} onRetry={search} />}

      {!loading && !error && data && (
        <div className="flex-1 min-h-0 flex">
          <aside className="w-96 border-r border-border overflow-y-auto bg-surface-raised">
            <div className="p-3 border-b border-border text-xs text-muted font-mono">
              {data.summary?.read_count ?? 0} reads · {data.summary?.camera_count ?? 0} cameras ·{" "}
              {data.summary?.impossible_hop_count ?? 0} impossible hops
            </div>
            <ol className="divide-y divide-border/60">
              {sightings.map((s, i) => (
                <li key={`${s.ts}-${s.camera_id}-${i}`} className="p-3 text-sm">
                  <div className="flex justify-between gap-2">
                    <span className="font-mono text-accent">{s.camera_id}</span>
                    <Badge>{formatConfidence(s.confidence)}</Badge>
                  </div>
                  <p className="text-muted text-xs mt-1">{formatTs(s.ts)}</p>
                  {s.direction && <p className="text-xs mt-1">Direction: {s.direction}</p>}
                </li>
              ))}
            </ol>
          </aside>

          <div className="flex-1 flex flex-col min-w-0">
            <div className="flex-1 relative min-h-0">
              <DeckMap
                layers={layers}
                getTooltip={({ object }) => {
                  if (!object) return null;
                  const o = object as unknown as TripPath;
                  if (o.impossible_hop) {
                    return {
                      html: `<b>Impossible hop</b><br/>${o.from_camera} → ${o.to_camera}`,
                    };
                  }
                  return null;
                }}
              />
            </div>
            <div className="shrink-0 border-t border-border bg-surface-raised px-4 py-3 flex items-center gap-4">
              <Button
                variant="secondary"
                size="sm"
                onClick={() => setPlaying((p) => !p)}
              >
                {playing ? "Pause" : "Play"}
              </Button>
              <input
                type="range"
                min={0}
                max={100}
                value={timeFrac * 100}
                onChange={(e) => {
                  setTimeFrac(Number(e.target.value) / 100);
                  setPlaying(false);
                }}
                className="flex-1"
              />
              <span className="text-xs font-mono text-muted w-40 text-right">
                {formatTs(new Date(currentTime).toISOString())}
              </span>
            </div>
          </div>
        </div>
      )}

      {!loading && !error && !data && (
        <div className="flex-1 flex items-center justify-center text-muted text-sm">
          Enter a plate and case ID to reconstruct a trajectory.
        </div>
      )}
    </div>
  );
}
