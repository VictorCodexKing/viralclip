"use client";

import * as React from "react";
import Link from "next/link";
import { ChevronRight } from "lucide-react";

import { listJobs, type Job } from "@/lib/api";

function statusColor(status: string): string {
  switch (status) {
    case "completed":
      return "text-green-600 dark:text-green-500";
    case "error":
      return "text-destructive";
    default:
      return "text-muted-foreground";
  }
}

export function JobsList() {
  const [jobs, setJobs] = React.useState<Job[] | null>(null);
  const [error, setError] = React.useState<string | null>(null);

  React.useEffect(() => {
    listJobs()
      .then((data) => setJobs(data.jobs))
      .catch((err: Error) => setError(err.message));
  }, []);

  if (error) {
    return (
      <div className="rounded-md border border-destructive/40 bg-destructive/10 px-4 py-3 text-sm text-destructive">
        {error}
      </div>
    );
  }

  if (jobs === null) {
    return <p className="text-sm text-muted-foreground">Loading…</p>;
  }

  if (jobs.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">
        No jobs yet. Start one from the home page.
      </p>
    );
  }

  return (
    <ul className="flex flex-col gap-2">
      {jobs.map((job) => (
        <li key={job.id}>
          <Link
            href={`/jobs/${job.id}`}
            className="flex items-center justify-between rounded-lg border bg-card px-4 py-3 hover:bg-accent"
          >
            <div className="flex flex-col gap-0.5 overflow-hidden">
              <span className="truncate text-sm font-medium">
                {job.source_url || `Uploaded video (${job.source_type})`}
              </span>
              <span className={`text-xs ${statusColor(job.status)}`}>
                {job.status} · {job.clips.length} clips
              </span>
            </div>
            <ChevronRight className="size-4 shrink-0 text-muted-foreground" />
          </Link>
        </li>
      ))}
    </ul>
  );
}
