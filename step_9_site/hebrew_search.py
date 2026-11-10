"""mkdocs hook: let the built-in search find Hebrew words written with a prefix letter.

Material's search matches the start of indexed words only, so ``קולומביה`` misses a section
that only says ``בקולומביה``. After the search plugin has written ``search/search_index.json``
this hook adds to every entry an extra field (``extra.hebrew_search.field``, registered with
the search plugin's ``fields`` option by step 9) holding the words of the entry with a prefix
(``ו``, ``ב``, ``וה``...) stripped. The field is searched but never displayed.

Loaded by mkdocs from its path (``hooks:`` in mkdocs.yml), so it imports nothing from this
project; its settings come from ``extra.hebrew_search`` (written by step 9 from config.json).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from mkdocs.config.defaults import MkDocsConfig
from mkdocs.plugins import event_priority

_HEBREW_WORD = re.compile(r"[א-ת]+")


def prefix_variants(text: str, prefixes: list[str], min_stem_length: int) -> list[str]:
    """Words of ``text`` with a leading prefix stripped, keeping at least ``min_stem_length``
    letters; words already present in ``text`` are left out."""
    words = set(_HEBREW_WORD.findall(text))
    variants = {
        word[len(prefix) :]
        for word in words
        for prefix in prefixes
        if word.startswith(prefix) and len(word) - len(prefix) >= min_stem_length
    }
    return sorted(variants - words)


@event_priority(-100)  # after the search plugin has written its index
def on_post_build(config: MkDocsConfig) -> None:
    settings = config.extra["hebrew_search"]
    path = Path(config.site_dir) / "search" / "search_index.json"
    index = json.loads(path.read_text(encoding="utf-8"))
    for doc in index["docs"]:
        text = f"{doc['title']} {doc['text']}"
        variants = prefix_variants(text, settings["prefixes"], settings["min_stem_length"])
        if variants:
            doc[settings["field"]] = " ".join(variants)
    path.write_text(json.dumps(index, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
