import { formatRelative } from "@shell";

// One row of the pipeline: the artifact, its state, and when the file the next step reads
// last changed (and whether that is the edited copy).

const DOTS = {
  sky: "bg-sky-500 animate-pulse",
  rose: "bg-rose-500",
  amber: "bg-amber-500",
  emerald: "bg-emerald-500",
  slate: "bg-slate-300",
};
const LABEL_TONES = {
  sky: "text-sky-800",
  rose: "text-rose-700",
  amber: "text-amber-800",
  emerald: "text-slate-500",
  slate: "text-slate-500",
};

export function StateLabel({ state }) {
  return (
    <span className={`flex items-center gap-1.5 ${LABEL_TONES[state.tone]}`}>
      <span aria-hidden="true" className={`h-2 w-2 shrink-0 rounded-full ${DOTS[state.tone]}`} />
      {state.label}
    </span>
  );
}

export function ArtifactRow({ artifact, state, now }) {
  const changed = artifact.edited_mtime ?? artifact.mtime;
  return (
    <span className="flex flex-col gap-0.5">
      <span className="truncate font-medium">{artifact.title}</span>
      <span className="flex items-center justify-between gap-2 text-xs">
        <StateLabel state={state} />
        {changed && (
          <span className="text-slate-500" title={changed}>
            {artifact.edited_exists ? "edited " : ""}
            {formatRelative(changed, now)}
          </span>
        )}
      </span>
    </span>
  );
}
