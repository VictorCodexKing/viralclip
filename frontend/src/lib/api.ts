/**
 * Typed fetch client for the ViralClip FastAPI backend.
 *
 * All shapes mirror the backend contract (see backend/app/api/routes/jobs.py
 * and backend/app/jobstore.py). The base URL comes from NEXT_PUBLIC_API_URL
 * and defaults to http://localhost:8000, matching the backend CORS allowlist.
 */

export const API_URL =
  process.env.NEXT_PUBLIC_API_URL?.replace(/\/$/, "") || "http://localhost:8000";

// --- Domain types -----------------------------------------------------------

export type JobStatus = "queued" | "processing" | "completed" | "error";

export type OutputFormat = "vertical" | "original";

export interface Clip {
  id: string;
  job_id: string;
  filename: string | null;
  file_path: string | null;
  start_time: number | null;
  end_time: number | null;
  duration: number | null;
  text: string | null;
  relevance_score: number | null;
  reasoning: string | null;
  virality_score: number | null;
  hook_score: number | null;
  engagement_score: number | null;
  value_score: number | null;
  shareability_score: number | null;
  hook_type: string | null;
  hook_title: string | null;
  clip_order: number;
  export_path: string | null;
}

export interface Job {
  id: string;
  source_url: string | null;
  source_type: string | null;
  status: JobStatus;
  progress: number;
  progress_message: string | null;
  error: string | null;
  created_at: number;
  updated_at: number;
  options: Record<string, unknown>;
  clips: Clip[];
}

export interface CaptionTemplate {
  id: string;
  name: string;
  description: string;
  animation: string;
  font_family: string;
  font_size: number;
  font_color: string;
  highlight_color: string;
}

export interface FontInfo {
  name: string;
  display_name: string;
  filename: string;
  format: string;
  file_path: string;
  scope: string;
}

export interface JobOptions {
  font_family?: string;
  font_size?: number;
  font_color?: string;
  caption_template?: string;
  include_broll?: boolean;
  output_format?: OutputFormat;
  add_subtitles?: boolean;
  transcription_provider?: string;
}

export interface CreateJobResponse {
  job_id: string;
  status: JobStatus;
}

export interface UploadResponse {
  source: { url: string };
  source_type: string;
  filename: string;
  path: string;
  size: number;
}

export type ExportPreset = "tiktok" | "reels" | "shorts";

export interface ExportResponse {
  preset: string;
  path: string;
  filename: string;
  url: string;
}

export interface ProgressEvent {
  job_id: string;
  status: JobStatus;
  progress: number | null;
  message: string;
}

// --- Low-level fetch helper --------------------------------------------------

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, {
    ...init,
    headers: {
      ...(init?.body && !(init.body instanceof FormData)
        ? { "Content-Type": "application/json" }
        : {}),
      ...init?.headers,
    },
  });

  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      if (body?.detail) {
        detail =
          typeof body.detail === "string"
            ? body.detail
            : JSON.stringify(body.detail);
      }
    } catch {
      // response was not JSON; keep the status line
    }
    throw new Error(detail);
  }

  return (await res.json()) as T;
}

// --- Jobs --------------------------------------------------------------------

export function createJob(
  sourceUrl: string,
  options: JobOptions,
): Promise<CreateJobResponse> {
  return request<CreateJobResponse>("/api/jobs", {
    method: "POST",
    body: JSON.stringify({ source: { url: sourceUrl }, options }),
  });
}

export function uploadVideo(file: File): Promise<UploadResponse> {
  const form = new FormData();
  form.append("file", file);
  return request<UploadResponse>("/api/uploads", {
    method: "POST",
    body: form,
  });
}

export function getJob(jobId: string): Promise<Job> {
  return request<Job>(`/api/jobs/${jobId}`);
}

export function listJobs(limit = 50): Promise<{ jobs: Job[]; total: number }> {
  return request(`/api/jobs?limit=${limit}`);
}

// --- Selector metadata -------------------------------------------------------

export function getTemplates(): Promise<{ templates: CaptionTemplate[] }> {
  return request("/api/templates");
}

export function getFonts(): Promise<{ fonts: FontInfo[] }> {
  return request("/api/fonts");
}

// --- Clip file URLs ----------------------------------------------------------

export function clipFileUrl(
  jobId: string,
  clipId: string,
  download = false,
): string {
  const base = `${API_URL}/api/jobs/${jobId}/clips/${clipId}/file`;
  return download ? `${base}?download=1` : base;
}

/** Absolute URL for a path the API returned relative to its own origin. */
export function apiUrl(path: string): string {
  return path.startsWith("http") ? path : `${API_URL}${path}`;
}

// --- Editor: trim / split / merge / export -----------------------------------

export function trimClip(
  jobId: string,
  clipId: string,
  startOffset: number,
  endOffset: number,
): Promise<{ clip: Clip }> {
  return request(`/api/jobs/${jobId}/clips/${clipId}`, {
    method: "PATCH",
    body: JSON.stringify({ start_offset: startOffset, end_offset: endOffset }),
  });
}

export function splitClip(
  jobId: string,
  clipId: string,
  splitTime: number,
): Promise<{ clips: Clip[] }> {
  return request(`/api/jobs/${jobId}/clips/${clipId}/split`, {
    method: "POST",
    body: JSON.stringify({ split_time: splitTime }),
  });
}

export function mergeClips(
  jobId: string,
  clipIds: string[],
): Promise<{ clip: Clip }> {
  return request(`/api/jobs/${jobId}/clips/merge`, {
    method: "POST",
    body: JSON.stringify({ clip_ids: clipIds }),
  });
}

export function exportClip(
  jobId: string,
  clipId: string,
  preset: ExportPreset,
): Promise<ExportResponse> {
  return request(`/api/jobs/${jobId}/clips/${clipId}/export`, {
    method: "POST",
    body: JSON.stringify({ preset }),
  });
}

// --- SSE progress ------------------------------------------------------------

export interface ProgressSubscription {
  close: () => void;
}

/**
 * Subscribe to live pipeline progress via EventSource. Handlers receive parsed
 * progress payloads; the connection auto-closes when the backend emits its
 * terminal "close" event or on a transport error.
 */
export function subscribeProgress(
  jobId: string,
  handlers: {
    onProgress?: (event: ProgressEvent) => void;
    onClose?: (status: JobStatus) => void;
    onError?: (error: Event) => void;
  },
): ProgressSubscription {
  const source = new EventSource(
    `${API_URL}/api/jobs/${jobId}/progress`,
  );

  const parse = (event: MessageEvent): ProgressEvent | null => {
    try {
      return JSON.parse(event.data) as ProgressEvent;
    } catch {
      return null;
    }
  };

  source.addEventListener("status", (event) => {
    const data = parse(event as MessageEvent);
    if (data) handlers.onProgress?.(data);
  });

  source.addEventListener("progress", (event) => {
    const data = parse(event as MessageEvent);
    if (data) handlers.onProgress?.(data);
  });

  source.addEventListener("close", (event) => {
    let status: JobStatus = "completed";
    try {
      const parsed = JSON.parse((event as MessageEvent).data);
      if (parsed?.status) status = parsed.status as JobStatus;
    } catch {
      // keep default
    }
    handlers.onClose?.(status);
    source.close();
  });

  source.onerror = (event) => {
    handlers.onError?.(event);
  };

  return {
    close: () => source.close(),
  };
}
