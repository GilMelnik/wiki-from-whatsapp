// The three states every panel has before it has something to show.
//
//   <PanelState loading={list.loading} error={list.error} onRetry={list.reload}
//               empty={list.data?.items.length === 0} emptyText="No claims match this filter">
//     ...the panel's real content...
//   </PanelState>
//
// Loading and error are shown only while there is no data to show instead (`hasData`), so
// a quiet refetch never blanks a panel.

function Centered({ tone = "text-slate-400", children }) {
  return (
    <div className={`flex flex-1 flex-col items-center justify-center gap-3 p-6 text-center text-sm ${tone}`}>
      {children}
    </div>
  );
}

export function PanelState({
  loading = false,
  error = null,
  empty = false,
  hasData = false,
  emptyText = "Nothing to show",
  loadingText = "Loading…",
  onRetry,
  children,
}) {
  if (error && !hasData) {
    return (
      <Centered tone="text-rose-700">
        <p dir="auto">{error.message || String(error)}</p>
        {onRetry && (
          <button type="button" className="btn btn-sm" onClick={onRetry}>
            Try again
          </button>
        )}
      </Centered>
    );
  }
  if (loading && !hasData) return <Centered>{loadingText}</Centered>;
  if (empty) return <Centered>{emptyText}</Centered>;
  return children;
}
