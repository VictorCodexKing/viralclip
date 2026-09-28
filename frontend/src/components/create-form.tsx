"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { Link2, Loader2, Sparkles, Upload, X } from "lucide-react";

import {
  createJob,
  getFonts,
  getTemplates,
  uploadVideo,
  type CaptionTemplate,
  type FontInfo,
  type JobOptions,
  type OutputFormat,
} from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { cn } from "@/lib/utils";

const FONT_SIZES = [24, 32, 40, 48, 56, 64];

export function CreateForm() {
  const router = useRouter();

  const [templates, setTemplates] = React.useState<CaptionTemplate[]>([]);
  const [fonts, setFonts] = React.useState<FontInfo[]>([]);
  const [metaError, setMetaError] = React.useState<string | null>(null);

  const [url, setUrl] = React.useState("");
  const [file, setFile] = React.useState<File | null>(null);
  const [dragActive, setDragActive] = React.useState(false);
  const [submitting, setSubmitting] = React.useState(false);
  const fileInputRef = React.useRef<HTMLInputElement>(null);

  // Options
  const [captionTemplate, setCaptionTemplate] = React.useState<string>("");
  const [fontFamily, setFontFamily] = React.useState<string>("");
  const [fontSize, setFontSize] = React.useState<number>(48);
  const [fontColor, setFontColor] = React.useState<string>("#FFFFFF");
  const [includeBroll, setIncludeBroll] = React.useState(false);
  const [outputFormat, setOutputFormat] =
    React.useState<OutputFormat>("vertical");
  const [addSubtitles, setAddSubtitles] = React.useState(true);

  React.useEffect(() => {
    let cancelled = false;
    Promise.all([getTemplates(), getFonts()])
      .then(([tpl, fnt]) => {
        if (cancelled) return;
        setTemplates(tpl.templates);
        setFonts(fnt.fonts);
        if (tpl.templates[0]) {
          setCaptionTemplate(tpl.templates[0].id);
          setFontFamily(tpl.templates[0].font_family);
          setFontSize(tpl.templates[0].font_size);
          setFontColor(tpl.templates[0].font_color);
        }
      })
      .catch((err: Error) => {
        if (cancelled) return;
        setMetaError(err.message);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // When the template changes, seed its defaults into the font controls.
  function onTemplateChange(id: string) {
    setCaptionTemplate(id);
    const tpl = templates.find((t) => t.id === id);
    if (tpl) {
      setFontFamily(tpl.font_family);
      setFontSize(tpl.font_size);
      setFontColor(tpl.font_color);
    }
  }

  function pickFile(f: File | null) {
    if (!f) return;
    setFile(f);
    setUrl("");
  }

  function onDrop(e: React.DragEvent) {
    e.preventDefault();
    setDragActive(false);
    const f = e.dataTransfer.files?.[0];
    if (f) pickFile(f);
  }

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (submitting) return;

    if (!url.trim() && !file) {
      toast.error("Paste a YouTube URL or choose a video file first.");
      return;
    }

    const options: JobOptions = {
      caption_template: captionTemplate || undefined,
      font_family: fontFamily || undefined,
      font_size: fontSize,
      font_color: fontColor,
      include_broll: includeBroll,
      output_format: outputFormat,
      add_subtitles: addSubtitles,
    };

    setSubmitting(true);
    try {
      let sourceUrl = url.trim();
      if (file) {
        toast.info("Uploading video…");
        const uploaded = await uploadVideo(file);
        sourceUrl = uploaded.source.url;
      }
      const { job_id } = await createJob(sourceUrl, options);
      toast.success("Job started. Watching progress…");
      router.push(`/jobs/${job_id}`);
    } catch (err) {
      toast.error(
        err instanceof Error ? err.message : "Failed to start the job.",
      );
      setSubmitting(false);
    }
  }

  return (
    <form onSubmit={onSubmit} className="flex flex-col gap-8">
      {metaError ? (
        <div className="rounded-md border border-destructive/40 bg-destructive/10 px-4 py-3 text-sm text-destructive">
          Could not reach the backend at the configured API URL: {metaError}
        </div>
      ) : null}

      {/* Source: URL or upload */}
      <section className="flex flex-col gap-4">
        <div className="flex flex-col gap-2">
          <Label htmlFor="url">
            <Link2 className="size-4" /> Paste a YouTube URL
          </Label>
          <input
            id="url"
            type="url"
            inputMode="url"
            placeholder="https://www.youtube.com/watch?v=…"
            value={url}
            disabled={!!file}
            onChange={(e) => setUrl(e.target.value)}
            className="border-input focus-visible:border-ring focus-visible:ring-ring/50 h-10 w-full rounded-md border bg-transparent px-3 text-sm shadow-xs outline-none focus-visible:ring-[3px] disabled:opacity-50"
          />
        </div>

        <div className="flex items-center gap-3 text-xs text-muted-foreground">
          <span className="h-px flex-1 bg-border" />
          OR UPLOAD A LONG VIDEO
          <span className="h-px flex-1 bg-border" />
        </div>

        <div
          onDragOver={(e) => {
            e.preventDefault();
            setDragActive(true);
          }}
          onDragLeave={() => setDragActive(false)}
          onDrop={onDrop}
          onClick={() => fileInputRef.current?.click()}
          className={cn(
            "flex cursor-pointer flex-col items-center justify-center gap-2 rounded-lg border border-dashed px-4 py-8 text-center transition-colors",
            dragActive ? "border-primary bg-accent" : "border-input",
            url.trim() ? "opacity-50 pointer-events-none" : "",
          )}
        >
          <input
            ref={fileInputRef}
            type="file"
            accept="video/mp4,video/x-matroska,video/webm,video/quicktime,.mp4,.mkv,.webm,.mov,.m4v,.avi"
            className="hidden"
            onChange={(e) => pickFile(e.target.files?.[0] ?? null)}
          />
          {file ? (
            <div className="flex items-center gap-2 text-sm">
              <span className="font-medium">{file.name}</span>
              <button
                type="button"
                onClick={(e) => {
                  e.stopPropagation();
                  setFile(null);
                  if (fileInputRef.current) fileInputRef.current.value = "";
                }}
                className="rounded-full p-1 hover:bg-muted"
                aria-label="Remove selected file"
              >
                <X className="size-4" />
              </button>
            </div>
          ) : (
            <>
              <Upload className="size-6 text-muted-foreground" />
              <p className="text-sm">
                Drag & drop a podcast, talk, or VOD, or click to browse
              </p>
              <p className="text-xs text-muted-foreground">
                mp4, mkv, webm, mov, m4v, avi
              </p>
            </>
          )}
        </div>
      </section>

      {/* Options */}
      <section className="grid gap-5 rounded-lg border bg-card p-5 sm:grid-cols-2">
        <div className="flex flex-col gap-2 sm:col-span-2">
          <Label>Caption template</Label>
          <Select value={captionTemplate} onValueChange={onTemplateChange}>
            <SelectTrigger>
              <SelectValue placeholder="Choose a template" />
            </SelectTrigger>
            <SelectContent>
              {templates.map((t) => (
                <SelectItem key={t.id} value={t.id}>
                  {t.name} — {t.description}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        <div className="flex flex-col gap-2">
          <Label>Font family</Label>
          <Select value={fontFamily} onValueChange={setFontFamily}>
            <SelectTrigger>
              <SelectValue placeholder="Font" />
            </SelectTrigger>
            <SelectContent>
              {fonts.map((f) => (
                <SelectItem key={f.name} value={f.name}>
                  {f.display_name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        <div className="flex flex-col gap-2">
          <Label>Font size</Label>
          <Select
            value={String(fontSize)}
            onValueChange={(v) => setFontSize(Number(v))}
          >
            <SelectTrigger>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {FONT_SIZES.map((s) => (
                <SelectItem key={s} value={String(s)}>
                  {s}px
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        <div className="flex flex-col gap-2">
          <Label htmlFor="font-color">Font color</Label>
          <div className="flex items-center gap-2">
            <input
              id="font-color"
              type="color"
              value={fontColor}
              onChange={(e) => setFontColor(e.target.value.toUpperCase())}
              className="h-9 w-12 cursor-pointer rounded-md border bg-transparent"
            />
            <span className="text-sm text-muted-foreground">{fontColor}</span>
          </div>
        </div>

        <div className="flex flex-col gap-2">
          <Label>Output format</Label>
          <Select
            value={outputFormat}
            onValueChange={(v) => setOutputFormat(v as OutputFormat)}
          >
            <SelectTrigger>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="vertical">Vertical 9:16</SelectItem>
              <SelectItem value="original">Original aspect</SelectItem>
            </SelectContent>
          </Select>
        </div>

        <div className="flex items-center justify-between rounded-md border px-3 py-2">
          <Label htmlFor="subtitles" className="cursor-pointer">
            Word-synced subtitles
          </Label>
          <Switch
            id="subtitles"
            checked={addSubtitles}
            onCheckedChange={setAddSubtitles}
          />
        </div>

        <div className="flex items-center justify-between rounded-md border px-3 py-2">
          <Label htmlFor="broll" className="cursor-pointer">
            Include B-roll
          </Label>
          <Switch
            id="broll"
            checked={includeBroll}
            onCheckedChange={setIncludeBroll}
          />
        </div>
      </section>

      <Button type="submit" size="lg" disabled={submitting} className="w-full">
        {submitting ? (
          <>
            <Loader2 className="size-4 animate-spin" /> Starting…
          </>
        ) : (
          <>
            <Sparkles className="size-4" /> Find viral clips
          </>
        )}
      </Button>
    </form>
  );
}
