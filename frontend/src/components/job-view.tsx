"use client";

import * as React from "react";
import Link from "next/link";
import {
  AlertTriangle,
  ArrowLeft,
  CheckCircle2,
  Clapperboard,
  Loader2,
} from "lucide-react";

import {
  getJob,
  subscribeProgress,
  type Job,
  type JobStatus,
} from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import { ClipGallery } from "@/components/clip-gallery";

export function JobView({ jobId }: { jobId: string }) {
  const [job, setJob] = React.useState<Job | null>(null);
  const [status, setStatus] = React.useState<JobStatus>("queued");
  const [progress, setProgress] = React.useState(0);
  const [message, setMessage] = React.useState("Starting…");
  const [error, setError] = React.useState<string | null>(null);
  const [loadError, setLoadError] = React.useState<string | null>(null);

  const refreshJob = React.useCallback(async () => {
    try {
      const data = await getJob(jobId);
      setJob(data);
      setStatus(data.status);
      setProgress(data.progress);
      if (data.error) setError(data.error);
      return data;
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : "Failed to load job.");
      return null;
    }
  }, [jobId]);

  const subRef = React.useRef<null | { close: () => void }>(null);

  React.useEffect(() => {
    let cancelled = false;

    // Load the initial snapshot, then subscribe to live progress unless the
    // job is already finished.
    refreshJob().then((data) => {
      if (cancelled || !data) return;
      if (data.status === "completed" || data.status === "error") return;

      const sub = subscribeProgress(jobId, {
        onProgress: (event) => {
          setStatus(event.status);
          if (event.progress != null) setProgress(event.progress);
          if (event.message) setMessage(event.message);
        },
        onClose: (finalStatus) => {
          setStatus(finalStatus);
          // Fetch the final job so clips (or the error) are available.
          refreshJob();
        },
        onError: () => {
          // On transport error, fall back to a one-off refresh.
          refreshJob();
        },
      });

      subRef.current = sub;
    });

    return () => {
      cancelled = true;
      subRef.current?.close();
    };
  }, [jobId, refreshJob]);

  const isDone = status === "completed";
  const isError = status === "error";
  const isRunning = !isDone && !isError;

  return (
    <main className="mx-auto flex min-h-screen w-full max-w-6xl flex-col gap-8 px-4 py-10">
      <header className="flex items-center justify-between">
        <Link
          href="/"
          className="flex items-center gap-2 text-lg font-semibold tracking-tight"
        >
          <Clapperboard className="size-6" />
          ViralClip
        </Link>
        <Button asChild variant="ghost" size="sm">
          <Link href="/">
            <ArrowLeft className="size-4" /> New job
          </Link>
        </Button>
      </header>

      {loadError ? (
        <div className="rounded-md border border-destructive/40 bg-destructive/10 px-4 py-3 text-sm text-destructive">
          {loadError}
        </div>
      ) : null}

      {/* Progress */}
      {isRunning ? (
        <section className="flex flex-col gap-4 rounded-xl border bg-card p-6">
          <div className="flex items-center gap-2">
            <Loader2 className="size-5 animate-spin text-muted-foreground" />
            <h1 className="text-lg font-semibold">Processing your video</h1>
          </div>
          <Progress value={progress} />
          <div className="flex items-center justify-between text-sm text-muted-foreground">
            <span>{message}</span>
            <span className="tabular-nums">{Math.round(progress)}%</span>
          </div>
          <p className="text-xs text-muted-foreground">
            Stages: download → transcribe → analyze → render → complete. You can
            leave this page open; progress streams live.
          </p>
        </section>
      ) : null}

      {isError ? (
        <section className="flex flex-col gap-3 rounded-xl border border-destructive/40 bg-destructive/10 p-6">
          <div className="flex items-center gap-2 text-destructive">
            <AlertTriangle className="size-5" />
            <h1 className="text-lg font-semibold">Processing failed</h1>
          </div>
          <p className="text-sm text-destructive">
            {error || job?.error || "The pipeline reported an error."}
          </p>
          <Button asChild variant="outline" size="sm" className="self-start">
            <Link href="/">Try another video</Link>
          </Button>
        </section>
      ) : null}

      {isDone ? (
        <>
          <section className="flex items-center gap-2 text-green-600 dark:text-green-500">
            <CheckCircle2 className="size-5" />
            <h1 className="text-lg font-semibold">Your clips are ready</h1>
          </section>
          {job ? (
            <ClipGallery
              jobId={jobId}
              clips={job.clips}
              onClipsChanged={refreshJob}
            />
          ) : null}
        </>
      ) : null}
    </main>
  );
}
