import { Bidi } from "@shell";

/** One row of the suggestion queue: both texts, and a mark when the merge needs a second look. */
export function SuggestionRow({ pair }) {
  const withheld = pair.a.withheld || pair.b.withheld;
  return (
    <span className="flex flex-col gap-1">
      {(pair.dispute || withheld) && (
        <span className="flex flex-wrap gap-1">
          {pair.dispute && <span className="badge badge-rose">They dispute each other</span>}
          {withheld && <span className="badge badge-amber">Withheld from the wiki</span>}
        </span>
      )}
      <Bidi as="span" className="line-clamp-2 block text-slate-700">
        {pair.a.claim_text}
      </Bidi>
      <Bidi as="span" className="line-clamp-2 block border-s-2 border-slate-200 ps-2 text-slate-600">
        {pair.b.claim_text}
      </Bidi>
    </span>
  );
}
