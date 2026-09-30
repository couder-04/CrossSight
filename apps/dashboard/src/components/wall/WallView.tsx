"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState, type MouseEvent } from "react";
import { Badge } from "@/components/ui/Badge";
import { api } from "@/lib/api";
import { canAccessTrack } from "@/lib/auth";
import { openWallSocket } from "@/lib/ws";
import type { Camera, FrameNotice, FrameTrack, Role } from "@/types";

export function WallView() {
  const router = useRouter();
  const [cameras, setCameras] = useState<Camera[]>([]);
  const [srcs, setSrcs] = useState<Record<string, string>>({});
  const [live, setLive] = useState<Record<string, boolean>>({});
  const [tracks, setTracks] = useState<Record<string, FrameTrack[]>>({});
  const [expanded, setExpanded] = useState<string | null>(null);
  const [role, setRole] = useState<Role | null>(null);
  const [error, setError] = useState<string | null>(null);
  const timers = useRef<Record<string, number>>({});
  const lastRefresh = useRef<Record<string, number>>({});
  const pendingTracks = useRef<Record<string, FrameTrack[]>>({});

  const refreshCamera = useCallback((cameraId: string, nextTracks: FrameTrack[]) => {
    pendingTracks.current[cameraId] = nextTracks;
    if (timers.current[cameraId]) return;
    const elapsed = Date.now() - (lastRefresh.current[cameraId] ?? 0);
    const wait = Math.max(0, 500 - elapsed);
    timers.current[cameraId] = window.setTimeout(() => {
      delete timers.current[cameraId];
      lastRefresh.current[cameraId] = Date.now();
      const tracksNow = pendingTracks.current[cameraId] ?? [];
      setTracks((prev) => ({ ...prev, [cameraId]: tracksNow }));
      setLive((prev) => ({ ...prev, [cameraId]: true }));
      api
        .cameraFrame(cameraId, Date.now())
        .then((frame) => {
          setSrcs((prev) => ({ ...prev, [cameraId]: frame.url }));
        })
        .catch(() => undefined);
    }, wait);
  }, []);

  useEffect(() => {
    let cancelled = false;
    fetch("/api/auth/me")
      .then((res) => (res.ok ? res.json() : null))
      .then((user: { role?: Role } | null) => {
        if (!cancelled && user?.role) setRole(user.role);
      })
      .catch(() => undefined);

    api
      .cameras()
      .then(async (cams) => {
        if (cancelled) return;
        setCameras(cams);
        const liveMap: Record<string, boolean> = {};
        const srcMap: Record<string, string> = {};
        await Promise.all(
          cams.map(async (cam) => {
            liveMap[cam.id] = Boolean(cam.has_live_frame);
            if (!cam.has_live_frame) return;
            try {
              const frame = await api.cameraFrame(cam.id, Date.now());
              srcMap[cam.id] = frame.url;
            } catch {
              liveMap[cam.id] = false;
            }
          }),
        );
        if (!cancelled) {
          setLive(liveMap);
          setSrcs(srcMap);
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof Error ? err.message : "Failed to load cameras");
      });

    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    let socket: { close: () => void } | null = null;
    let off: () => void = () => undefined;
    let closed = false;

    openWallSocket()
      .then((liveSocket) => {
        if (closed) {
          liveSocket.close();
          return;
        }
        socket = liveSocket;
        liveSocket.connect();
        off = liveSocket.onMessage((msg) => {
          if (msg.channel !== "frames") return;
          const data = msg.data as FrameNotice;
          if (!data?.camera_id) return;
          refreshCamera(data.camera_id, data.tracks ?? []);
        });
      })
      .catch((err: unknown) => {
        if (!closed) setError(err instanceof Error ? err.message : "Wall stream unavailable");
      });

    return () => {
      closed = true;
      off();
      socket?.close();
      Object.values(timers.current).forEach((id) => window.clearTimeout(id));
      timers.current = {};
    };
  }, [refreshCamera]);

  const allowTrack = role != null && canAccessTrack(role);
  const expandedCam = cameras.find((cam) => cam.id === expanded) ?? null;

  return (
    <div className="h-full overflow-auto p-4">
      <div className="flex items-baseline justify-between mb-4">
        <h1 className="text-sm font-semibold uppercase tracking-wide text-slate-200">Camera wall</h1>
        <p className="text-xs text-muted">{cameras.length} cameras</p>
      </div>
      {error && <p className="text-sm text-danger mb-3">{error}</p>}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        {cameras.map((cam) => (
          <button
            key={cam.id}
            type="button"
            onClick={() => setExpanded(cam.id)}
            className="text-left rounded-lg border border-border bg-surface-raised overflow-hidden hover:border-accent/60"
          >
            <div className="relative aspect-video bg-black">
              {srcs[cam.id] ? (
                <img src={srcs[cam.id]} alt="" className="w-full h-full object-cover" />
              ) : (
                <div className="w-full h-full flex items-center justify-center text-xs text-muted">
                  No frame
                </div>
              )}
              {(live[cam.id] || cam.has_live_frame) && (
                <span className="absolute top-2 left-2">
                  <Badge tone="success">Live</Badge>
                </span>
              )}
            </div>
            <div className="px-3 py-2 text-sm truncate">{cam.name}</div>
          </button>
        ))}
      </div>

      {expandedCam && (
        <div
          className="fixed inset-0 z-50 bg-black/75 flex items-center justify-center p-4"
          onClick={() => setExpanded(null)}
        >
          <div
            className="relative w-full max-w-5xl rounded-lg border border-border bg-surface-raised overflow-hidden"
            onClick={(event) => event.stopPropagation()}
          >
            <div className="flex items-center justify-between px-4 py-3 border-b border-border">
              <h2 className="text-sm font-semibold">{expandedCam.name}</h2>
              <button
                type="button"
                className="text-xs text-muted hover:text-slate-100"
                onClick={() => setExpanded(null)}
              >
                Close
              </button>
            </div>
            <ExpandedFrame
              src={srcs[expandedCam.id]}
              tracks={tracks[expandedCam.id] ?? []}
              allowTrack={allowTrack}
              onPlate={(plate) => router.push(`/track?plate=${encodeURIComponent(plate)}`)}
            />
          </div>
        </div>
      )}
    </div>
  );
}

function ExpandedFrame({
  src,
  tracks,
  allowTrack,
  onPlate,
}: {
  src?: string;
  tracks: FrameTrack[];
  allowTrack: boolean;
  onPlate: (plate: string) => void;
}) {
  const imgRef = useRef<HTMLImageElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);

  const paint = useCallback(() => {
    const canvas = canvasRef.current;
    const img = imgRef.current;
    if (!canvas || !img || !img.naturalWidth) return;
    canvas.width = img.naturalWidth;
    canvas.height = img.naturalHeight;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    ctx.strokeStyle = "#4ade80";
    ctx.lineWidth = 2;
    for (const track of tracks) {
      if (!track.bbox || track.bbox.length < 4) continue;
      const [x1, y1, x2, y2] = track.bbox;
      ctx.strokeRect(x1, y1, x2 - x1, y2 - y1);
    }
  }, [tracks]);

  useEffect(() => {
    paint();
  }, [paint, src]);

  function onClick(event: MouseEvent<HTMLCanvasElement>) {
    const img = imgRef.current;
    const canvas = canvasRef.current;
    if (!img || !canvas || !img.naturalWidth) return;
    const rect = canvas.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) return;
    const x = ((event.clientX - rect.left) / rect.width) * img.naturalWidth;
    const y = ((event.clientY - rect.top) / rect.height) * img.naturalHeight;
    const hit = tracks.find((track) => {
      if (!track.plate_norm || !track.bbox || track.bbox.length < 4) return false;
      const [x1, y1, x2, y2] = track.bbox;
      const left = Math.min(x1, x2);
      const right = Math.max(x1, x2);
      const top = Math.min(y1, y2);
      const bottom = Math.max(y1, y2);
      return x >= left && x <= right && y >= top && y <= bottom;
    });
    if (!hit || !allowTrack) return;
    onPlate(hit.plate_norm);
  }

  return (
    <div className="relative bg-black">
      {src ? (
        <img
          ref={imgRef}
          src={src}
          alt=""
          className="w-full max-h-[75vh] object-contain"
          onLoad={paint}
        />
      ) : (
        <div className="aspect-video flex items-center justify-center text-sm text-muted">
          Waiting for a frame
        </div>
      )}
      <canvas
        ref={canvasRef}
        className={`absolute inset-0 w-full h-full ${allowTrack ? "cursor-crosshair" : "cursor-not-allowed"}`}
        title={allowTrack ? "Open trajectory" : "Analysts cannot open plate trajectories"}
        onClick={onClick}
      />
    </div>
  );
}
