// A column's content: fixed header, scrolling body, fixed footer for the main actions.
//
//   <Panel title="Actions" subtitle="Decisions are saved at once" footer={<button className="btn btn-primary btn-block">Accept</button>}>
//     ...
//   </Panel>

export function Panel({ title, subtitle, actions, footer, className = "", children }) {
  const hasHeader = Boolean(title || subtitle || actions);
  return (
    <section className={`flex min-h-0 flex-1 flex-col ${className}`}>
      {hasHeader && (
        <header className="panel-header flex items-start justify-between gap-3">
          <div className="min-w-0">
            {title && <h2 className="text-sm font-semibold">{title}</h2>}
            {subtitle && <p className="mt-0.5 text-xs text-slate-500">{subtitle}</p>}
          </div>
          {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
        </header>
      )}
      <div className="flex min-h-0 flex-1 flex-col overflow-y-auto">{children}</div>
      {footer && <footer className="panel-footer">{footer}</footer>}
    </section>
  );
}
