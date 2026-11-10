// Every tool the web app knows, in the order the header shows them. To add a tool: create
// `src/tools/<id>/index.jsx` (default export `{ id, title, Component }`, `id` equal to the
// Python `ToolSpec.name`) and add one import line here.

import status from "./status/index.jsx";

export const TOOLS = [status];
