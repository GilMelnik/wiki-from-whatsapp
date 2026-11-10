import { formatMinute } from "../goldWindows";

/** Dashed line in the message viewer where the selected gold window starts or ends. */
export default function GoldDivider({ id, entering, gold }) {
  const label = entering
    ? `▼ תחילת חלון הזהב (${formatMinute(gold.start)})`
    : `▲ סוף חלון הזהב (${formatMinute(gold.end)})`;
  return (
    <div id={id} className="flex items-center gap-2 py-1 text-xs font-semibold text-yellow-800">
      <span className="flex-1 border-t-2 border-dashed border-yellow-500" />
      <span>{label}</span>
      <span className="flex-1 border-t-2 border-dashed border-yellow-500" />
    </div>
  );
}
