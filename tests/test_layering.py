"""The code's three layers, checked on every tracked ``*.py`` file (the owner's rule, task 63).

1. ``utils/`` is infrastructure: it imports no step, no ``review_tool`` and not ``pipeline``.
2. A step (``step_1_…`` to ``step_9_…``) imports ``utils``, itself and EARLIER steps only:
   never a later step, ``step_10_update``, ``review_tool`` or ``pipeline``. What a step uses
   first lives in that step; later steps import it from there.
3. ``pipeline.py``, ``step_10_update`` and ``review_tool`` run the steps and may import them.

The web app has the same kind of guard (``review_tool/web/scripts/check-imports.mjs``).
"""

from __future__ import annotations

import ast
import re
import subprocess
from pathlib import Path

from utils.config import PROJECT_ROOT

ORCHESTRATION = ("step_10_update", "review_tool", "pipeline")
STEP = re.compile(r"step_(\d+)_")
# (importing file, imported module): why it is allowed.
EXCEPTIONS = {
    ("step_4_entities/evaluate.py", "review_tool.tools.entities.store"): (
        "step 4's measurement replays reviewer edits through the entities tool's store; it is "
        "a command of its own (python -m step_4_entities.evaluate), on no run path"
    ),
}


def _tracked_python_files() -> list[str]:
    listed = subprocess.run(["git", "ls-files", "*.py"], cwd=PROJECT_ROOT, capture_output=True,
                            text=True, check=True)
    return listed.stdout.split()


def _imported_modules(path: Path) -> list[str]:
    modules = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules.append(node.module)
    return modules


def _step_number(top: str) -> int | None:
    match = STEP.match(top)
    return int(match.group(1)) if match and top not in ORCHESTRATION else None


def _breaks_layer(file: str, module: str) -> bool:
    layer, top = file.split("/")[0], module.split(".")[0]
    if layer == "utils":
        return _step_number(top) is not None or top in ORCHESTRATION
    importer = _step_number(layer)
    if importer is None:
        return False
    imported = _step_number(top)
    return top in ORCHESTRATION or (imported is not None and imported > importer)


def test_no_import_breaks_the_layers() -> None:
    offending = [
        f"{file}: imports {module}"
        for file in _tracked_python_files()
        for module in _imported_modules(PROJECT_ROOT / file)
        if _breaks_layer(file, module) and (file, module) not in EXCEPTIONS
    ]
    assert not offending, "imports that break the layers:\n" + "\n".join(offending)


def test_every_exception_is_still_needed() -> None:
    for file, module in EXCEPTIONS:
        assert module in _imported_modules(PROJECT_ROOT / file), f"{file} no longer imports {module}"
