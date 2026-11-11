// Every tool the web app knows, in the order the header shows them. To add a tool: create
// `src/tools/<id>/index.jsx` (default export `{ id, title, Component }`, `id` equal to the
// Python `ToolSpec.name`; optional `quietStale`: artifact keys whose stale notice the tool
// words itself) and add one import line here.

import entities from "./entities/index.jsx";
import groups from "./groups/index.jsx";
import pii from "./pii/index.jsx";
import plan from "./plan/index.jsx";
import status from "./status/index.jsx";
import suggestions from "./suggestions/index.jsx";

export const TOOLS = [status, pii, entities, groups, plan, suggestions];
