import Link from "next/link";
import { Clapperboard, History } from "lucide-react";

import { CreateForm } from "@/components/create-form";
import { Button } from "@/components/ui/button";

export default function HomePage() {
  return (
    <main className="mx-auto flex min-h-screen w-full max-w-2xl flex-col gap-10 px-4 py-12">
      <header className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Clapperboard className="size-6" />
          <span className="text-lg font-semibold tracking-tight">
            ViralClip
          </span>
        </div>
        <Button asChild variant="ghost" size="sm">
          <Link href="/jobs">
            <History className="size-4" /> Recent jobs
          </Link>
        </Button>
      </header>

      <div className="flex flex-col gap-3">
        <h1 className="text-3xl font-bold tracking-tight">
          Turn long videos into viral vertical clips
        </h1>
        <p className="text-muted-foreground">
          Paste a YouTube link or upload a podcast, talk, or stream VOD. An LLM
          picks the most clip-worthy moments, scores their virality, crops them
          to 9:16, and burns in word-synced subtitles and hook titles.
        </p>
      </div>

      <CreateForm />
    </main>
  );
}
