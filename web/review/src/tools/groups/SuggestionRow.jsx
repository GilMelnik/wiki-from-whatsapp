import { Bidi, formatCount, Ltr } from "@shell";
import { formatScore } from "./groups.js";

/** One row of the suggestion queue: both texts, sizes and the weakest similarity. */
export function SuggestionRow({ pair }) {
  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-center justify-between gap-2 text-xs text-slate-500">
        <span>
          {formatCount(pair.a.size)} + {formatCount(pair.b.size)} claims
          {pair.shared_entity && <span className="badge badge-slate ms-2">Shared entity</span>}
        </span>
        <Ltr>{formatScore(pair.min_cosine)}</Ltr>
      </div>
      <Bidi as="p" className="line-clamp-2 text-slate-700">
        {pair.a.claim_text}
      </Bidi>
      <Bidi as="p" className="line-clamp-2 border-s-2 border-slate-200 ps-2 text-slate-600">
        {pair.b.claim_text}
      </Bidi>
    </div>
  );
}
