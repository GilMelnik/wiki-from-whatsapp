"""The one list of review tools the server hosts, in the order the header shows them.

To add a tool: put its code in ``review_tool/tools/<name>/`` (an empty ``__init__.py``),
import its ``ToolSpec`` here and add it to ``TOOL_SPECS`` (one line each).
A tool's module must not load its data at import time; it reaches its store through a
``review_tool.stores.StoreHandle`` on first use.
"""

from __future__ import annotations

from review_tool.artifacts import GRAPH
from review_tool.registry import ToolSpec
from review_tool.steps import SERVED_STEPS
from review_tool.tools.entities.tool import EntitiesTool
from review_tool.tools.groups.tool import GroupsTool
from review_tool.tools.pii.tool import PiiTool
from review_tool.tools.plan.tool import PlanTool
from review_tool.tools.status.tool import StatusTool
from review_tool.tools.status.update_status import UpdateStatus
from review_tool.tools.suggestions.tool import SuggestionsTool
from review_tool.tools.threads.tool import ThreadsTool
from utils.config import DATA_DIR
from utils.paths import UPDATE_LEASE_PATH, UPDATE_STATE_PATH

TOOL_SPECS: tuple[ToolSpec, ...] = (
    StatusTool(
        GRAPH, DATA_DIR, UpdateStatus(UPDATE_STATE_PATH, UPDATE_LEASE_PATH, SERVED_STEPS.steps, GRAPH)
    ).spec,
    ThreadsTool(GRAPH).spec,
    PiiTool(GRAPH).spec,
    EntitiesTool(GRAPH).spec,
    GroupsTool(GRAPH).spec,
    PlanTool(GRAPH).spec,
    SuggestionsTool(GRAPH).spec,
)
