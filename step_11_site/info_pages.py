"""The pages step 11 adds that are not wiki content: the terms page, the accessibility statement and
the about page.

All are plain Hebrew markdown. The terms text and the statement live in source files of the
repository (``site.terms.source``, ``site.accessibility.source``) that are copied with their
placeholders filled (the contact address, the statement's date); the about page is generated
from ``site.about``, and the model of every step is read from ``config.json`` (the dotted paths
in each step's ``models``), so the page stays true when a model changes.
"""

from __future__ import annotations

from typing import Any

from utils.config import PROJECT_ROOT, ROOT_CONFIG, section

SITE_CONFIG = section("site")
ABOUT_CONFIG = SITE_CONFIG["about"]
TERMS_CONFIG = SITE_CONFIG["terms"]
STATEMENT_CONFIG = SITE_CONFIG["accessibility"]


def config_value(path: str) -> Any:
    """The value at a dotted path of ``config.json``, e.g. ``llm_client.stages.plan.model``."""
    value: Any = ROOT_CONFIG
    for part in path.split("."):
        if not isinstance(value, dict) or part not in value:
            raise KeyError(f"config.json has nothing at {path!r} (stopped at {part!r})")
        value = value[part]
    return value


def _source_text(page: dict[str, Any]) -> str:
    """A page's text from its source file (``page["source"]``), with ``{contact_email}`` filled
    from ``site.contact_email`` (the address lives in config.json only)."""
    source = PROJECT_ROOT / page["source"]
    if not source.is_file():
        raise FileNotFoundError(f"the source of the page {page['title']!r} is missing: {source}")
    text = source.read_text(encoding="utf-8")
    return text.replace(SITE_CONFIG["contact_placeholder"], SITE_CONFIG["contact_email"])


def terms_body() -> str:
    """The terms page."""
    return _source_text(TERMS_CONFIG)


def statement_body() -> str:
    """The accessibility statement, dated ``site.accessibility.updated``."""
    return _source_text(STATEMENT_CONFIG).replace(STATEMENT_CONFIG["updated_placeholder"], STATEMENT_CONFIG["updated"])


def _step_lines(number: int, step: dict[str, Any]) -> list[str]:
    models = [f"`{config_value(path)}`" for path in step["models"]]
    lines = [f"{number}. **{step['title']}.** {step['text']}", ""]
    if models:
        label = ABOUT_CONFIG["models_label" if len(models) > 1 else "model_label"]
        lines += [f"    {label} {', '.join(models)}", ""]
    if step["reviewed"]:
        lines += [f"    {ABOUT_CONFIG['reviewed_text']}", ""]
    return lines


def about_body() -> str:
    """The "how this wiki was made" page: the principles, the steps and their models, the links."""
    principles = [f"- {principle}" for principle in ABOUT_CONFIG["principles"]]
    steps = [line for number, step in enumerate(ABOUT_CONFIG["steps"], 1) for line in _step_lines(number, step)]
    links = [f"- [{link['text']}]({link['url']})" for link in ABOUT_CONFIG["links"]]
    return "\n".join(
        [
            ABOUT_CONFIG["intro"],
            "",
            f"## {ABOUT_CONFIG['principles_heading']}",
            "",
            *principles,
            "",
            f"## {ABOUT_CONFIG['steps_heading']}",
            "",
            *steps,
            f"## {ABOUT_CONFIG['links_heading']}",
            "",
            *links,
            "",
            f"## {ABOUT_CONFIG['tools_heading']}",
            "",
            ABOUT_CONFIG["tools_text"],
        ]
    )
