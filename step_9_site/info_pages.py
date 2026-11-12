"""The two pages step 9 adds that are not wiki content: the terms page and the about page.

Both are plain Hebrew markdown. The terms text lives in a source file of the repository
(``site.terms.source``) that is copied as it is; the about page is generated from ``site.about``,
and the model of every step is read from ``config.json`` (the dotted paths in each step's
``models``), so the page stays true when a model changes.
"""

from __future__ import annotations

from typing import Any

from utils.config import PROJECT_ROOT, ROOT_CONFIG, section

SITE_CONFIG = section("site")
ABOUT_CONFIG = SITE_CONFIG["about"]
TERMS_CONFIG = SITE_CONFIG["terms"]


def config_value(path: str) -> Any:
    """The value at a dotted path of ``config.json``, e.g. ``llm_client.stages.plan.model``."""
    value: Any = ROOT_CONFIG
    for part in path.split("."):
        if not isinstance(value, dict) or part not in value:
            raise KeyError(f"config.json has nothing at {path!r} (stopped at {part!r})")
        value = value[part]
    return value


def terms_body() -> str:
    """The terms text from its source file, with ``{contact_email}`` filled from
    ``site.contact_email`` (the address lives in config.json only)."""
    source = PROJECT_ROOT / TERMS_CONFIG["source"]
    if not source.is_file():
        raise FileNotFoundError(f"the terms page source is missing: {source}")
    text = source.read_text(encoding="utf-8")
    return text.replace(TERMS_CONFIG["contact_placeholder"], SITE_CONFIG["contact_email"])


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
