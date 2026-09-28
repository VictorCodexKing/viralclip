"use client";

import * as React from "react";
import { toast } from "sonner";
import {
  Download,
  Loader2,
  Scissors,
  SplitSquareHorizontal,
} from "lucide-react";

import {
  apiUrl,
  clipFileUrl,
  exportClip,
  splitClip,
  trimClip,
  type Clip,
  type ExportPreset,
} from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Slider } from "@/components/ui/slider";
import { ScoreBar } from "@/components/score-bar";

const EXPORT_PRESETS: { id: ExportPreset; label: string }[] = [
  { id: "tiktok", label: "TikTok" },
  { id: "reels", label: "Reels" },
  { id: "shorts", label: "Shorts" },
];

function formatSeconds(value: number | null): string {
  if (value == null) return "0:00";
  const total = Math.max(0, Math.round(value));
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${m}:${s.toString().padStart(2, "0")}`;
}

export function ClipCard({
  clip,
  jobId,
  index,
  selected,
  onToggleSelect,
  onClipsChanged,
}: {
  clip: Clip;
  jobId: string;
  index: number;
  selected: boolean;
  onToggleSelect: (clipId: string) => void;
  onClipsChanged: () => void;
}) {
  const duration = clip.duration ?? 0;

  const [busy, setBusy] = React.useState(false);
  const [trim, setTrim] = React.useState<[number, number]>([0, 0]);
  const [splitTime, setSplitTime] = React.useState<number>(
    duration > 0 ? Math.round(duration / 2) : 0,
  );
  // Cache-buster so the <video> reloads after a trim regenerates the file.
  const [version, setVersion] = React.useState(0);

  const total =
    clip.virality_score != null ? Math.round(clip.virality_score) : null;

  const src = `${clipFileUrl(jobId, clip.id)}${
    version ? `?v=${version}` : ""
  }`;

  async function handleTrim() {
    setBusy(true);
    try {
      await trimClip(jobId, clip.id, trim[0], trim[1]);
      toast.success("Clip trimmed and regenerated.");
      setVersion((v) => v + 1);
      setTrim([0, 0]);
      onClipsChanged();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Trim failed.");
    } finally {
      setBusy(false);
    }
  }

  async function handleSplit() {
    if (splitTime <= 0 || splitTime >= duration) {
      toast.error("Pick a split point inside the clip.");
      return;
    }
    setBusy(true);
    try {
      await splitClip(jobId, clip.id, splitTime);
      toast.success("Clip split into two.");
      onClipsChanged();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Split failed.");
    } finally {
      setBusy(false);
    }
  }

  async function handleExport(preset: ExportPreset) {
    setBusy(true);
    try {
      const result = await exportClip(jobId, clip.id, preset);
      toast.success(`Exported for ${preset}. Downloading…`);
      // The export endpoint returns a download URL (relative to the API)
      // that serves the preset-encoded artifact, not the original clip.
      const link = document.createElement("a");
      link.href = apiUrl(result.url);
      link.download = result.filename;
      document.body.appendChild(link);
      link.click();
      link.remove();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Export failed.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-col gap-4 rounded-xl border bg-card p-4">
      <div className="flex items-start justify-between gap-2">
        <div className="flex items-center gap-2">
          <input
            type="checkbox"
            checked={selected}
            onChange={() => onToggleSelect(clip.id)}
            className="size-4 accent-[var(--color-primary)]"
            aria-label={`Select clip ${index + 1} for merge`}
          />
          <span className="text-sm font-medium">Clip {index + 1}</span>
        </div>
        {total != null ? (
          <span className="rounded-full bg-primary px-2.5 py-0.5 text-xs font-semibold text-primary-foreground">
            {total}/100
          </span>
        ) : null}
      </div>

      {clip.hook_title ? (
        <p className="text-base font-semibold leading-snug">
          {clip.hook_title}
        </p>
      ) : null}

      {/* 9:16 preview */}
      <div className="mx-auto w-full max-w-[240px]">
        <video
          key={src}
          controls
          preload="metadata"
          className="aspect-[9/16] w-full rounded-lg bg-black object-contain"
        >
          <source src={src} type="video/mp4" />
        </video>
      </div>

      {/* Virality breakdown */}
      <div className="grid grid-cols-2 gap-x-4 gap-y-2">
        <ScoreBar label="Hook" value={clip.hook_score} />
        <ScoreBar label="Engagement" value={clip.engagement_score} />
        <ScoreBar label="Value" value={clip.value_score} />
        <ScoreBar label="Shareability" value={clip.shareability_score} />
      </div>

      {clip.hook_type ? (
        <p className="text-xs text-muted-foreground">
          Hook type: <span className="font-medium">{clip.hook_type}</span>
        </p>
      ) : null}

      {clip.reasoning ? (
        <p className="text-sm text-muted-foreground">{clip.reasoning}</p>
      ) : null}

      {/* Editor */}
      <div className="flex flex-col gap-4 rounded-lg border bg-background p-3">
        <div className="flex flex-col gap-2">
          <Label className="justify-between">
            <span className="flex items-center gap-1.5">
              <Scissors className="size-3.5" /> Trim (seconds off each end)
            </span>
            <span className="text-xs text-muted-foreground tabular-nums">
              −{trim[0].toFixed(1)}s start · −{trim[1].toFixed(1)}s end
            </span>
          </Label>
          <div className="flex items-center gap-2">
            <span className="w-8 text-xs text-muted-foreground">Start</span>
            <Slider
              value={[trim[0]]}
              min={0}
              max={Math.max(0, Math.floor(duration))}
              step={0.5}
              onValueChange={([v]) => setTrim([v, trim[1]])}
            />
          </div>
          <div className="flex items-center gap-2">
            <span className="w-8 text-xs text-muted-foreground">End</span>
            <Slider
              value={[trim[1]]}
              min={0}
              max={Math.max(0, Math.floor(duration))}
              step={0.5}
              onValueChange={([v]) => setTrim([trim[0], v])}
            />
          </div>
          <Button
            variant="outline"
            size="sm"
            disabled={busy || (trim[0] === 0 && trim[1] === 0)}
            onClick={handleTrim}
          >
            {busy ? <Loader2 className="size-4 animate-spin" /> : null}
            Apply trim
          </Button>
        </div>

        <div className="flex flex-col gap-2">
          <Label className="justify-between">
            <span className="flex items-center gap-1.5">
              <SplitSquareHorizontal className="size-3.5" /> Split at
            </span>
            <span className="text-xs text-muted-foreground tabular-nums">
              {formatSeconds(splitTime)}
            </span>
          </Label>
          <Slider
            value={[splitTime]}
            min={0}
            max={Math.max(0, Math.floor(duration))}
            step={0.5}
            onValueChange={([v]) => setSplitTime(v)}
          />
          <Button
            variant="outline"
            size="sm"
            disabled={busy}
            onClick={handleSplit}
          >
            Split clip
          </Button>
        </div>
      </div>

      {/* Export + download */}
      <div className="flex flex-wrap items-center gap-2">
        {EXPORT_PRESETS.map((p) => (
          <Button
            key={p.id}
            variant="secondary"
            size="sm"
            disabled={busy}
            onClick={() => handleExport(p.id)}
          >
            {p.label}
          </Button>
        ))}
        <Button asChild size="sm" className="ml-auto">
          <a href={clipFileUrl(jobId, clip.id, true)} download>
            <Download className="size-4" /> Download
          </a>
        </Button>
      </div>
    </div>
  );
}
