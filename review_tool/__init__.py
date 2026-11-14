"""One server and one web app for every review tool (contract C10); all their code is here.

Run ``python -m review_tool --tool <name>``. Where things are:

- ``review_tool/tools/<name>/``: one tool's server side (its ``tool.py`` builds the
  ``ToolSpec``; the package's ``__init__.py`` stays empty so that importing one tool module
  never loads another tool)
- ``review_tool.toolset``: ``TOOL_SPECS``, the tools the server hosts (a tool is hosted by
  adding it there)

What a tool author imports:

- ``review_tool.registry``: ``ToolSpec``
- ``review_tool.artifacts``: ``GRAPH`` (artifact keys, files, staleness), ``ArtifactNode``;
  a new artifact is one ``ArtifactNode`` line in ``artifacts._pipeline_nodes``
- ``review_tool.stores``: ``StoreHandle`` (a store that reloads when its files change)
- ``review_tool.locks``: ``artifact_lock``, ``save_json``, ``ArtifactLockTimeout``
- ``review_tool.events``: ``publish``
- ``review_tool.steps``: the step registry (``STEP_SPECS``; a step migrated to contract C14
  sets ``incremental_safe=True`` in its row so the tools may re-run it)
- ``review_tool.jobs``: ``JobRunner.run_step`` (a step as a subprocess of the server)

The pipeline's steps and ``pipeline.py`` import ``review_tool.steps``, ``artifacts``, ``port``
and ``settings``, never ``review_tool.tools``.
"""
