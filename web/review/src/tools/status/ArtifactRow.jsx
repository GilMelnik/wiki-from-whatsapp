import { formatRelative, Ltr } from "@shell";

/** The badges that sum up one artifact's state (also used by the detail header). */
export function StateBadges({ artifact }) {
  const anyFile = artifact.exists || artifact.edited_exists;
  return (
    <span className="flex flex-wrap gap-1">
      {!anyFile && <span className="badge badge-slate">חסר</span>}
      {artifact.edited_exists && <span className="badge badge-sky">עותק ערוך</span>}
      {artifact.stale_because.length > 0 && <span className="badge badge-amber">לא עדכני</span>}
      {artifact.exists && artifact.step && artifact.stale_because.length === 0 && (
        <span className="badge badge-emerald">עדכני</span>
      )}
    </span>
  );
}

export function ArtifactRow({ artifact, now }) {
  const changed = artifact.edited_mtime ?? artifact.mtime;
  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-center justify-between gap-2">
        <span className="truncate font-medium">{artifact.title}</span>
        <Ltr className="text-xs text-slate-400">{artifact.key}</Ltr>
      </div>
      <div className="flex items-center justify-between gap-2 text-xs text-slate-500">
        <StateBadges artifact={artifact} />
        <span title={changed ?? undefined}>{changed ? formatRelative(changed, now) : ""}</span>
      </div>
    </div>
  );
}
