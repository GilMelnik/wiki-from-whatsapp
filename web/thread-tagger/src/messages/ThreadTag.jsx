/** Coloured letter chip that names a thread (colour + letter, readable without colour). */
export default function ThreadTag({ info, label, title }) {
  if (!info) return null;
  return (
    <span
      title={title}
      className="inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[11px] font-bold leading-none"
      style={{ backgroundColor: info.color, color: info.ink }}
    >
      {info.tag}
      {label && <span className="font-mono font-medium">{label}</span>}
    </span>
  );
}
