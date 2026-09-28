import Link from "next/link";
import { ArrowLeft, Clapperboard } from "lucide-react";

import { JobsList } from "@/components/jobs-list";
import { Button } from "@/components/ui/button";

export default function JobsPage() {
  return (
    <main className="mx-auto flex min-h-screen w-full max-w-2xl flex-col gap-8 px-4 py-10">
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

      <h1 className="text-2xl font-bold tracking-tight">Recent jobs</h1>
      <JobsList />
    </main>
  );
}
