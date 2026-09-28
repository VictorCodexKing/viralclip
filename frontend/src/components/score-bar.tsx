import { cn } from "@/lib/utils";

/** A labelled sub-score bar (each sub-metric is 0-25). */
export function ScoreBar({
  label,
  value,
  max = 25,
}: {
  label: string;
  value: number | null;
  max?: number;
}) {
  const pct = value == null ? 0 : Math.max(0, Math.min(100, (value / max) * 100));
  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-center justify-between text-xs">
        <span className="text-muted-foreground">{label}</span>
        <span className="font-medium tabular-nums">
          {value == null ? "—" : Math.round(value)}
        </span>
      </div>
      <div className="h-1.5 w-full overflow-hidden rounded-full bg-secondary">
        <div
          className={cn("h-full rounded-full bg-primary transition-all")}
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}
