import { Bidi, formatBytes, formatCount, formatDateTime, formatRelative, Ltr, Panel, PanelState } from "@shell";
import { StateBadges } from "./ArtifactRow.jsx";

const ROLE_LABELS = { original: "מקורי (פלט השלב)", edited: "ערוך (אחרי סקירה)" };

function KeyChips({ keys, byKey, onSelect, emptyText }) {
  if (keys.length === 0) return <p className="text-slate-400">{emptyText}</p>;
  return (
    <div className="flex flex-wrap gap-2">
      {keys.map((key) => (
        <button key={key} type="button" className="btn btn-sm" onClick={() => onSelect(key)}>
          {byKey[key]?.title ?? key}
        </button>
      ))}
    </div>
  );
}

function StaleNotice({ artifact, byKey, onSelect }) {
  if (!artifact.exists && !artifact.edited_exists) {
    return <p className="rounded border border-slate-200 bg-white p-3">הקובץ עוד לא נוצר.</p>;
  }
  if (artifact.stale_because.length === 0) return null;
  return (
    <div className="space-y-2 rounded border border-amber-200 bg-amber-50 p-3 text-amber-900">
      <p>קבצים שהשלב קורא השתנו אחרי שהוא רץ, ולכן התוכן כאן אולי לא משקף אותם:</p>
      <KeyChips keys={artifact.stale_because} byKey={byKey} onSelect={onSelect} emptyText="" />
    </div>
  );
}

function FilesTable({ files, now }) {
  return (
    <table className="w-full text-start">
      <thead className="text-xs text-slate-500">
        <tr>
          <th className="py-1 pe-3 text-start font-normal">קובץ</th>
          <th className="py-1 pe-3 text-start font-normal">נתיב</th>
          <th className="py-1 pe-3 text-start font-normal">שינוי אחרון</th>
          <th className="py-1 text-start font-normal">גודל</th>
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
              {file.exists ? `${formatDateTime(file.mtime)} (${formatRelative(file.mtime, now)})` : "לא קיים"}
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
    return <p className="text-slate-400">אין בלוק metadata בקובץ.</p>;
  }
  return (
    <table className="w-full">
      <tbody className="divide-y divide-slate-100">
        {Object.entries(metadata).map(([name, value]) => (
          <tr key={name}>
            <td className="py-1 pe-3 align-top">
              <Ltr>{name}</Ltr>
            </td>
            <td className="py-1 break-all">
              {value && typeof value === "object" && "count" in value ? (
                `${formatCount(value.count)} פריטים`
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

function Section({ title, children }) {
  return (
    <section>
      <h3 className="section-title">{title}</h3>
      {children}
    </section>
  );
}

/** Main column: the selected artifact's state, files, inputs, consumers and metadata. */
export function ArtifactDetail({ artifact, detail, byKey, artifacts, now, onSelect }) {
  if (!artifact) return <PanelState empty emptyText="בחרו קובץ מהרשימה" />;
  const downstream = artifacts.filter((other) => other.upstream.includes(artifact.key)).map((other) => other.key);
  return (
    <Panel
      title={artifact.title}
      subtitle={artifact.step ? <>נוצר בשלב <Ltr>{artifact.step}</Ltr></> : "לא נוצר בשלב של הצינור"}
      actions={<StateBadges artifact={artifact} />}
    >
      <div className="space-y-6 p-4 text-sm">
        <StaleNotice artifact={artifact} byKey={byKey} onSelect={onSelect} />
        <PanelState loading={detail.loading} error={detail.error} hasData={Boolean(detail.data)} onRetry={detail.reload}>
          {detail.data && (
            <>
              <Section title="קבצים">
                <FilesTable files={detail.data.files} now={now} />
              </Section>
              <Section title="נבנה מתוך">
                <KeyChips keys={artifact.upstream} byKey={byKey} onSelect={onSelect} emptyText="קלט חיצוני" />
              </Section>
              <Section title="משמש את">
                <KeyChips keys={downstream} byKey={byKey} onSelect={onSelect} emptyText="אף שלב עדיין" />
              </Section>
              {artifact.step && (
                <Section title="metadata">
                  <MetadataTable metadata={detail.data.metadata} error={detail.data.metadata_error} />
                </Section>
              )}
            </>
          )}
        </PanelState>
      </div>
    </Panel>
  );
}
