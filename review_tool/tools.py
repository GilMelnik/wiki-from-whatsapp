"""The one list of review tools the server hosts, in the order the header shows them.

To add a tool: import its ``ToolSpec`` here and add it to ``TOOL_SPECS`` (one line each).
A tool's module must not load its data at import time; it reaches its store through a
``review_tool.stores.StoreHandle`` on first use.
"""

from __future__ import annotations

from review_tool.artifacts import GRAPH
from review_tool.registry import ToolSpec
from review_tool.status_tool import StatusTool
from review_tool.steps import SERVED_STEPS
from review_tool.update_status import UpdateStatus
from step_1_threads_split.reviewer.tool import ThreadsTool
from step_3_extract.reviewer.tool import PiiTool
from step_4_entities.reviewer.tool import EntitiesTool
from step_5_aggregate.reviewer.tool import GroupsTool
from step_6_plan.reviewer.tool import PlanTool
from step_8_review.reviewer.tool import SuggestionsTool
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
