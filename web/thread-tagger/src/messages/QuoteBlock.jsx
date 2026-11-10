/** A quoted message inside a message row; optional jump to the quoted row. */
export default function QuoteBlock({ quote, quotedIndex, onJumpToQuote }) {
  return (
    <div className="mb-2 pr-3 border-r-4 border-slate-300 bg-slate-50 rounded-r px-2 py-1.5 text-xs">
      <div className="text-slate-500 mb-0.5 flex items-center gap-2 flex-wrap">
        <span className="font-medium">ציטוט</span>
        {quote.sender && <span>· {quote.sender}</span>}
        {quotedIndex != null && onJumpToQuote && (
          <button
            type="button"
            onClick={(e) => {
              e.preventDefault();
              e.stopPropagation();
              onJumpToQuote(quotedIndex);
            }}
            className="text-blue-600 hover:underline"
          >
            → [m{quotedIndex}]
          </button>
        )}
      </div>
      <div className="text-slate-600 whitespace-pre-wrap break-words line-clamp-4">
        {quote.text}
      </div>
    </div>
  );
}
