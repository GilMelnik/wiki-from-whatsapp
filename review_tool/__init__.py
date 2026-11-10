"""One server and one web app for every review tool (contract C10).

Run ``python -m review_tool --tool <name>``. What a tool author imports:

- ``review_tool.registry``: ``ToolSpec`` (a tool is registered by adding it to ``TOOL_SPECS``)
- ``review_tool.artifacts``: ``GRAPH`` (artifact keys, files, staleness), ``ArtifactNode``;
  a new artifact is one ``ArtifactNode`` line in ``artifacts._pipeline_nodes``
- ``review_tool.stores``: ``StoreHandle`` (a store that reloads when its files change)
- ``review_tool.locks``: ``artifact_lock``, ``save_json``, ``ArtifactLockTimeout``
- ``review_tool.events``: ``publish``
- ``review_tool.steps``: the step registry (``STEP_SPECS``; a step migrated to contract C14
  sets ``incremental_safe=True`` in its row so the tools may re-run it)
- ``review_tool.jobs``: ``JobRunner.run_step`` (a step as a subprocess of the server)

The list of tools the server hosts is ``review_tool.tools.TOOL_SPECS``.
"""
