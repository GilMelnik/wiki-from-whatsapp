import { Bidi } from "@shell";
import { typeLabel } from "./review.js";

// A claim's scrubbed text with its marks highlighted. `segments` come from the server:
// `{text}` pieces and `{mark: i}` (i = the redaction it stands for, null for a mark without
// one). In "scrubbed" mode every mark stays a numbered "[הוסר]" chip; in "preview" mode the
// marks listed in `restored` show their value instead, as the text would be saved.

const MARK = "[הוסר]";

function Mark({ index, redaction }) {
  return (
    <mark
      className="rounded bg-rose-100 px-1 font-medium text-rose-800"
      title={redaction ? `${typeLabel(redaction.type)}: ${redaction.value}` : "סימון בלי פרט שהוסר"}
    >
      {MARK}
      {redaction && <span className="ms-0.5 text-[10px]">{index + 1}</span>}
    </mark>
  );
}

function Value({ redaction }) {
  return (
    <mark className="rounded bg-emerald-100 px-1 font-medium text-emerald-900" title={typeLabel(redaction.type)}>
      <bdi dir="ltr">{redaction.value}</bdi>
    </mark>
  );
}

export function ClaimText({ segments, redactions, restored = [], mode }) {
  return (
    <Bidi as="p" className="leading-relaxed">
      {segments.map((segment, position) => {
        if ("text" in segment) return <span key={position}>{segment.text}</span>;
        const redaction = segment.mark === null ? null : redactions[segment.mark];
        if (mode === "preview" && redaction && restored.includes(segment.mark)) {
          return <Value key={position} redaction={redaction} />;
        }
        return <Mark key={position} index={segment.mark} redaction={redaction} />;
      })}
    </Bidi>
  );
}
