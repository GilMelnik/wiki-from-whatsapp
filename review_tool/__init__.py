"""One server and one web app for every review tool (contract C10).

Run ``python -m review_tool --tool <name>``. What a tool author imports:

- ``review_tool.registry``: ``ToolSpec``, ``register``
- ``review_tool.artifacts``: ``GRAPH`` (artifact keys, files, staleness), ``ArtifactNode``
- ``review_tool.stores``: ``StoreHandle`` (a store that reloads when its files change)
- ``review_tool.locks``: ``artifact_lock``, ``save_json``, ``ArtifactLockTimeout``
- ``review_tool.events``: ``publish``

The list of tools the server hosts is ``review_tool.tools.TOOL_SPECS``.
"""
