"use client";

import * as React from "react";
import Link from "next/link";
import { toast } from "sonner";
import {
  AlertTriangle,
  ArrowLeft,
  Bell,
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
  const [notificationPermission, setNotificationPermission] = React.useState<NotificationPermission | null>(null);
  const notifiedJobs = React.useRef(new Set<string>());

  React.useEffect(() => {
    if ("Notification" in window) setNotificationPermission(Notification.permission);
  }, []);

  async function enableNotifications() {
    try {
      const permission = await Notification.requestPermission();
      setNotificationPermission(permission);
      if (permission === "granted") {
        toast.success("We'll notify you when your clips are ready.");
      } else {
        toast.info("You will still see a ready alert on this page.");
      }
    } catch {
      toast.info("Desktop notifications aren't available. We'll show the ready alert here.");
    }
  }

  React.useEffect(() => {
    if (job?.id !== jobId || job.status !== "completed" || job.clips.length === 0) return;
    if (notifiedJobs.current.has(jobId)) return;
    notifiedJobs.current.add(jobId);
    const storageKey = `viralclip:ready:${jobId}`;
    try {
      if (sessionStorage.getItem(storageKey)) return;
      sessionStorage.setItem(storageKey, "1");
    } catch {
      // The in-memory guard still prevents repeats when storage is unavailable.
    }
    const description = `${job.clips.length} ${job.clips.length === 1 ? "clip is" : "clips are"} ready to preview and download.`;
    toast.success("Your clips are ready!", { id: storageKey, description, duration: 10000 });
    if ("Notification" in window && Notification.permission === "granted") {
      try {
        const notification = new Notification("ViralClip: your clips are ready!", {
          body: description,
          tag: storageKey,
        });
        notification.onclick = () => {
          window.focus();
          notification.close();
        };
      } catch {
        // A browser may restrict desktop alerts; the in-page toast remains.
      }
    }
  }, [job, jobId]);

  const refreshJob = React.useCallback(async () => {
    try {
      const data = await getJob(jobId);
      setJob(data);
      setStatus(data.status);
      setProgress(data.progress);
      if (data.progress_message) setMessage(data.progress_message);
      setLoadError(null);
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
    let pollTimer: ReturnType<typeof setInterval> | undefined;

    const stopPolling = () => {
      if (pollTimer !== undefined) clearInterval(pollTimer);
    };

    // Load the initial snapshot, then subscribe to live progress unless the
    // job is already finished.
    refreshJob().then((data) => {
      if (cancelled || !data) return;
      if (data.status === "completed" || data.status === "error") return;

      // A snapshot fallback catches completion even if an SSE connection drops.
      pollTimer = setInterval(() => {
        refreshJob().then((snapshot) => {
          if (snapshot?.status === "completed" || snapshot?.status === "error") stopPolling();
        });
      }, 5000);

      const sub = subscribeProgress(jobId, {
        onProgress: (event) => {
          if (cancelled) return;
          setStatus(event.status);
          if (event.progress != null) setProgress(event.progress);
          if (event.message) setMessage(event.message);
        },
        onClose: (finalStatus) => {
          if (cancelled) return;
          setStatus(finalStatus);
          // Fetch the final job so clips (or the error) are available.
          refreshJob().then((snapshot) => {
            if (snapshot?.status === "completed" || snapshot?.status === "error") stopPolling();
          });
        },
        onError: () => {
          if (cancelled) return;
          // On transport error, fall back to a one-off refresh.
          refreshJob();
        },
      });

      subRef.current = sub;
    });

    return () => {
      cancelled = true;
      stopPolling();
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
          {notificationPermission !== null ? (
            <Button
              type="button"
              variant="outline"
              size="sm"
              className="self-start"
              onClick={enableNotifications}
              disabled={notificationPermission !== "default"}
            >
              <Bell className="size-4" />
              {notificationPermission === "granted"
                ? "Desktop notification enabled"
                : notificationPermission === "denied"
                  ? "Ready alert will appear on this page"
                  : "Notify me when ready"}
            </Button>
          ) : null}
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
