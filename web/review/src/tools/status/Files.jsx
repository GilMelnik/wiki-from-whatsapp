import { Bidi, formatBytes, formatCount, formatDateTime, formatRelative, Ltr } from "@shell";

// The files behind an artifact and the `metadata` block of the one the next step reads:
// for looking at a file by hand, so folded away by default.

const ROLE_LABELS = { original: "Step output", edited: "Edited copy" };

function FilesTable({ files, now }) {
  return (
    <table className="w-full text-start">
      <thead className="text-xs text-slate-500">
        <tr>
          <th className="py-1 pe-3 text-start font-normal">File</th>
          <th className="py-1 pe-3 text-start font-normal">Path</th>
          <th className="py-1 pe-3 text-start font-normal">Last change</th>
          <th className="py-1 text-start font-normal">Size</th>
        </tr>
      </thead>
      <tbody className="divide-y divide-slate-100">
        {files.map((file) => (
          <tr key={file.role} className={file.exists ? "" : "text-slate-400"}>
            <td className="py-1.5 pe-3">{ROLE_LABELS[file.role] ?? file.role}</td>
            <td className="py-1.5 pe-3">
              <Ltr className="break-all">{file.path}</Ltr>
            </td>
            <td className="py-1.5 pe-3" title={file.mtime ?? undefined}>
              {file.exists ? `${formatDateTime(file.mtime)} (${formatRelative(file.mtime, now)})` : "No file"}
            </td>
            <td className="py-1.5">{file.exists ? <Ltr>{formatBytes(file.size)}</Ltr> : "—"}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function MetadataTable({ metadata, error }) {
  if (error) return <p dir="auto" className="text-rose-700">{error}</p>;
  if (!metadata || Object.keys(metadata).length === 0) {
    return <p className="text-slate-400">The file has no metadata block.</p>;
  }
  return (
    <table className="w-full">
      <tbody className="divide-y divide-slate-100">
        {Object.entries(metadata).map(([name, value]) => (
          <tr key={name}>
            <td className="py-1 pe-3 align-top">
              <Ltr>{name}</Ltr>
            </td>
            <td className="break-all py-1">
              {value && typeof value === "object" && "count" in value ? (
                `${formatCount(value.count)} entries`
              ) : (
                <Bidi>{JSON.stringify(value)}</Bidi>
              )}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function Files({ artifact, data, now }) {
  return (
    <details className="group">
      <summary className="section-title mb-0 cursor-pointer select-none">Files and metadata</summary>
      <div className="mt-3 space-y-4">
        <FilesTable files={data.files} now={now} />
        {artifact.step && <MetadataTable metadata={data.metadata} error={data.metadata_error} />}
      </div>
    </details>
  );
}
