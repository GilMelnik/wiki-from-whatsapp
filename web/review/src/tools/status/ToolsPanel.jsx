import { Panel, ToolLink, useTools } from "@shell";

function roleOf(tool, key) {
  const reads = tool.reads.includes(key);
  const writes = tool.writes.includes(key);
  if (reads && writes) return "קורא וכותב";
  return writes ? "כותב" : "קורא";
}

/** Side column: the tools that read or write the selected artifact, with links to open them. */
export function ToolsPanel({ artifact }) {
  const { tools } = useTools();
  const related = artifact
    ? tools.filter((tool) => tool.reads.includes(artifact.key) || tool.writes.includes(artifact.key))
    : [];
  return (
    <Panel title="כלים" subtitle="כלי סקירה שקוראים או כותבים את הקובץ">
      <div className="space-y-2 p-4 text-sm">
        {related.length === 0 && <p className="text-slate-400">אף כלי עדיין לא עובד עם הקובץ הזה.</p>}
        {related.map((tool) => (
          <div key={tool.name} className="flex items-center justify-between gap-2 rounded border border-slate-200 bg-white p-2">
            <div>
              <div className="font-medium">{tool.title}</div>
              <div className="text-xs text-slate-500">{roleOf(tool, artifact.key)}</div>
            </div>
            <div className="flex gap-1">
              <ToolLink toolId={tool.name} className="btn btn-sm">
                פתיחה
              </ToolLink>
              <ToolLink toolId={tool.name} newTab className="btn btn-sm" title="בלשונית חדשה">
                ↗
              </ToolLink>
            </div>
          </div>
        ))}
      </div>
    </Panel>
  );
}
