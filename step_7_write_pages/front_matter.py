"""A page's OKF front matter, built in code with ``utils.okf.build_front_matter``.

Where each value comes from: ``title`` / ``description`` / ``aliases`` from the C7 page;
``path`` = the page's breadcrumb ``[{id, title}]``; ``labels`` by ``LabelRule``; ``sources`` =
the CITED groups (each footnote has its source; ``usage_count`` = supporters + opposers,
``last_modified`` = the group's last date); ``claims`` over all the page's groups;
``entities`` = the most frequent entity names of the cited groups (``pages.max_entities``);
``summary`` / ``questions`` / ``related`` from the model after validation (``related`` kept
to planned pages); ``generated`` = ``pages.generated_by`` / write-stage model, ``at`` = when
the text last changed (the caller keeps it when the text did not change).

The label rule (``pages.labels``, chosen on the dev corpus): a page carries label L when at
least ``min_groups`` of its groups carry L, L is at least ``min_lift`` times more frequent on
the page than in the corpus, and either ``min_page_share`` of the page's groups carry L or
the page holds ``min_label_share`` of all groups carrying L. A label that no page carries by
that rule goes to the page with most of its groups (if it has ``min_groups`` there), so
every label with enough material has a label page. A label on 29 % of all groups therefore
lands only on the pages where it is concentrated.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any

from step_6_plan.model import find_node
from step_7_write_pages.checks import cited_ids
from step_7_write_pages.inputs import PAGES_CONFIG, Corpus, PageContext
from utils.okf import build_front_matter

LABEL_RULE = PAGES_CONFIG["labels"]


class LabelRule:
    """The labels of every page of one plan (the rule in the module docstring)."""

    def __init__(self, corpus: Corpus, page_ids: Sequence[str]) -> None:
        placed = corpus.placed()
        totals = corpus.label_shares()
        group_count = max(len(placed), 1)
        counts = {pid: Counter(lab for gid in corpus.page_group_ids(pid) for lab in placed[gid]["labels"])
                  for pid in page_ids}
        sizes = {pid: len(corpus.page_group_ids(pid)) for pid in page_ids}
        carried: dict[str, list[str]] = {pid: [] for pid in page_ids}
        for pid in page_ids:
            for label_id in corpus.plan["labels"]:
                count = counts[pid][label_id]
                if count < LABEL_RULE["min_groups"] or not totals[label_id]:
                    continue
                page_share = count / sizes[pid]
                lift = page_share / (totals[label_id] / group_count)
                if lift >= LABEL_RULE["min_lift"] and (
                        page_share >= LABEL_RULE["min_page_share"]
                        or count / totals[label_id] >= LABEL_RULE["min_label_share"]):
                    carried[pid].append(label_id)
        homeless = [lab for lab in corpus.plan["labels"]
                    if not any(lab in labels for labels in carried.values())]
        for label_id in homeless:
            best = max(page_ids, key=lambda pid: (counts[pid][label_id],
                                                  counts[pid][label_id] / max(sizes[pid], 1)),
                       default=None)
            if best is not None and counts[best][label_id] >= LABEL_RULE["min_groups"]:
                carried[best].append(label_id)
        order = list(corpus.plan["labels"])
        self.labels = {pid: sorted(labels, key=order.index) for pid, labels in carried.items()}
        self.plan = corpus.plan

    def __call__(self, page_id: str) -> list[dict[str, str]]:
        return [{"id": lab, "kind": self.plan["labels"][lab]["kind"]}
                for lab in self.labels.get(page_id, [])]


def _entities(records: Sequence[Mapping[str, Any]]) -> list[str]:
    counts = Counter(name for record in records for name in dict.fromkeys(record["entities"]))
    return [name for name, _ in counts.most_common(PAGES_CONFIG["max_entities"])]


def page_front_matter(context: PageContext, plan: Mapping[str, Any], *, body: str, summary: str,
                      questions: list[str], related: list[dict[str, str]],
                      labels: list[dict[str, str]], model: str, generated_at: str) -> dict[str, Any]:
    """The validated front matter of one page (see the module docstring)."""
    page = plan["pages"][context.page_id]
    records = {record["id"]: record for record in context.records}
    cited = [records[gid] for gid in cited_ids(body) if gid in records]
    all_records = list(context.records)
    prefix = PAGES_CONFIG["source_resource_prefix"]
    return build_front_matter(
        title=page["title_he"],
        description=" ".join(page["description"].split()),
        tags=[],
        generated_by=f"{PAGES_CONFIG['generated_by']}/{model}",
        generated_at=generated_at,
        sources=[{"id": r["id"], "resource": f"{prefix}{r['id']}",
                  "usage_count": r["supporters"] + r["opposers"], "last_modified": r["last"]}
                 for r in cited],
        aliases={"he": list(page.get("aliases", {}).get("he", [])),
                 "en": list(page.get("aliases", {}).get("en", []))},
        entities=_entities(cited),
        claims={
            "total": len(all_records),
            "supporting": sum(r["supporters"] for r in all_records),
            "opposing": sum(r["opposers"] for r in all_records),
            "first_claim_at": min(r["first"] for r in all_records),
            "last_claim_at": max(r["last"] for r in all_records),
        },
        related=[r for r in related if r["slug"] in plan["pages"] and r["slug"] != context.page_id],
        path=[{"id": node_id, "title": find_node(plan, node_id)[0]["title_he"]}
              for node_id in page["path"]],
        summary=summary or None,
        questions=questions or None,
        labels=labels or None,
    )
