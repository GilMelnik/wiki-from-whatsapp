from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from threads_split.hebrew_tokenizer import HebrewTokenizer
from threads_split.pipeline import load_messages
from threads_split.tfidf import IDF_FORMULA, compute_idf


def _aggregate_term_stats(
    tokenized_messages: list[list[str]],
) -> tuple[int, int, dict[str, int], dict[str, int]]:
    document_count = 0
    total_tokens = 0
    term_tf: Counter[str] = Counter()
    term_df: defaultdict[str, int] = defaultdict(int)

    for tokens in tokenized_messages:
        if not tokens:
            continue

        document_count += 1
        total_tokens += len(tokens)
        term_tf.update(tokens)
        for term in set(tokens):
            term_df[term] += 1

    return document_count, total_tokens, dict(term_tf), dict(term_df)


def _build_corpus_payload(
    source_path: Path,
    document_count: int,
    total_tokens: int,
    term_tf: dict[str, int],
    term_df: dict[str, int],
    tokenizer_model: str,
) -> dict[str, Any]:
    default_idf = compute_idf(document_count, 0)
    terms: dict[str, dict[str, float | int]] = {}
    for term, tf in term_tf.items():
        df = term_df[term]
        terms[term] = {
            "df": df,
            "tf": tf,
            "idf": compute_idf(document_count, df),
        }

    return {
        "metadata": {
            "source": str(source_path),
            "document_count": document_count,
            "total_tokens": total_tokens,
            "unique_term_count": len(terms),
            "tokenizer_model": tokenizer_model,
            "idf_formula": IDF_FORMULA,
            "default_idf": default_idf,
        },
        "terms": terms,
    }


def write_tfidf_corpus(payload: dict[str, Any], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def run(
    input_path: Path | str = Path("data/messages_combined.json"),
    output_path: Path | str = Path("data/tfidf_corpus.json"),
    batch_size: int = 32,
    max_messages: int | None = None,
    tokenizer: HebrewTokenizer | None = None,
) -> dict[str, Any]:
    source_path = Path(input_path)
    output = Path(output_path)
    tokenizer = tokenizer or HebrewTokenizer(batch_size=batch_size)

    messages = load_messages(source_path)
    if max_messages is not None:
        messages = messages[:max_messages]

    contents = [message.content for message in messages]
    tokenized_messages = tokenizer.tokenize_batch(contents)
    document_count, total_tokens, term_tf, term_df = _aggregate_term_stats(tokenized_messages)
    payload = _build_corpus_payload(
        source_path=source_path,
        document_count=document_count,
        total_tokens=total_tokens,
        term_tf=term_tf,
        term_df=term_df,
        tokenizer_model=tokenizer.model_name,
    )
    write_tfidf_corpus(payload, output)

    return {
        "metadata": payload["metadata"],
        "output_path": str(output),
    }


if __name__ == "__main__":
    result = run()
    metadata = result["metadata"]
    print(
        f"Wrote TF-IDF corpus to {result['output_path']} "
        f"({metadata['document_count']} documents, "
        f"{metadata['unique_term_count']} unique terms)"
    )
