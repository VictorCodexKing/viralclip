"use client";

import * as React from "react";
import { toast } from "sonner";
import { Combine, Loader2 } from "lucide-react";

import { mergeClips, type Clip } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { ClipCard } from "@/components/clip-card";

export function ClipGallery({
  jobId,
  clips,
  onClipsChanged,
}: {
  jobId: string;
  clips: Clip[];
  onClipsChanged: () => void;
}) {
  const [selected, setSelected] = React.useState<string[]>([]);
  const [merging, setMerging] = React.useState(false);

  function toggleSelect(clipId: string) {
    setSelected((prev) =>
      prev.includes(clipId)
        ? prev.filter((id) => id !== clipId)
        : [...prev, clipId],
    );
  }

  async function handleMerge() {
    if (selected.length < 2) {
      toast.error("Select at least two clips to merge.");
      return;
    }
    setMerging(true);
    try {
      await mergeClips(jobId, selected);
      toast.success("Selected clips merged into a new clip.");
      setSelected([]);
      onClipsChanged();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Merge failed.");
    } finally {
      setMerging(false);
    }
  }

  if (clips.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">
        No clips were produced for this job.
      </p>
    );
  }

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-center justify-between">
        <h2 className="text-xl font-semibold">
          {clips.length} viral {clips.length === 1 ? "clip" : "clips"}
        </h2>
        <Button
          variant="outline"
          size="sm"
          disabled={selected.length < 2 || merging}
          onClick={handleMerge}
        >
          {merging ? (
            <Loader2 className="size-4 animate-spin" />
          ) : (
            <Combine className="size-4" />
          )}
          Merge selected ({selected.length})
        </Button>
      </div>

      <div className="grid gap-6 sm:grid-cols-2 lg:grid-cols-3">
        {clips.map((clip, index) => (
          <ClipCard
            key={clip.id}
            clip={clip}
            jobId={jobId}
            index={index}
            selected={selected.includes(clip.id)}
            onToggleSelect={toggleSelect}
            onClipsChanged={onClipsChanged}
          />
        ))}
      </div>
    </div>
  );
}
