import { Bidi } from "@shell";
import { colour, segments } from "./entities.js";

// A claim's text with its entity mentions highlighted: the member's own name in the member's
// colour, other entities in theirs (a click opens that entity).

export function ClaimText({ text, highlights, memberIndex, onOpenEntity }) {
  return (
    <Bidi as="p" text={text} className="leading-relaxed">
      {segments(text, highlights).map((piece, index) => {
        const { highlight } = piece;
        if (!highlight) return <span key={index}>{piece.text}</span>;
        if (highlight.kind === "self") {
          return (
            <mark key={index} className={`rounded px-0.5 font-semibold ${colour(memberIndex).mark}`}>
              {piece.text}
            </mark>
          );
        }
        return (
          <button
            key={index}
            type="button"
            title={`Open the entity “${highlight.canonical_name}”`}
            onClick={() => onOpenEntity(highlight.entity_id)}
            className={`rounded px-0.5 font-semibold underline underline-offset-2 hover:opacity-80 ${colour(highlight.color_index).mark}`}
          >
            {piece.text}
          </button>
        );
      })}
    </Bidi>
  );
}
