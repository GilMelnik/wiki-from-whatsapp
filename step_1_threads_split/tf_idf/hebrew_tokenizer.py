from __future__ import annotations

import re
from typing import Any, Sequence

_SPECIAL_TOKENS = {"[CLS]", "[SEP]"}
_KEEP_TOKEN_RE = re.compile(r"[\u0590-\u05FFa-zA-Z0-9]")


def _is_valid_token(token: str) -> bool:
    if not token or token in _SPECIAL_TOKENS:
        return False
    return _KEEP_TOKEN_RE.search(token) is not None


def _flatten_segments(segments: Sequence[str]) -> list[str]:
    return [segment for segment in segments if _is_valid_token(segment)]


def _cuda_device_is_supported() -> bool:
    import torch

    if not torch.cuda.is_available() or torch.cuda.device_count() == 0:
        return False

    major, minor = torch.cuda.get_device_capability(0)
    device_arch = f"sm_{major}{minor}"
    arch_list = torch.cuda.get_arch_list()
    if device_arch in arch_list:
        return True

    # e.g. sm_60 wheels also run on sm_61 Pascal hardware.
    return f"sm_{major}0" in arch_list


def _resolve_device(device_arg: str | None):
    import torch

    if device_arg is not None:
        return torch.device(device_arg)
    if _cuda_device_is_supported():
        return torch.device("cuda")
    return torch.device("cpu")


class HebrewTokenizer:
    def __init__(
        self,
        model_name: str = "dicta-il/dictabert-joint",
        batch_size: int = 32,
        device: str | None = None,
        model_kwargs: dict[str, Any] | None = None,
    ):
        self.model_name = model_name
        self.batch_size = batch_size
        self._device_arg = device
        self._model_kwargs = model_kwargs or {}
        self._tokenizer = None
        self._model = None
        self._device = None

    @property
    def device(self):
        if self._device is None:
            self._device = _resolve_device(self._device_arg)
        return self._device

    @property
    def tokenizer(self):
        if self._tokenizer is None:
            from transformers import AutoTokenizer

            self._tokenizer = AutoTokenizer.from_pretrained(self.model_name)
        return self._tokenizer

    @property
    def model(self):
        if self._model is None:
            from transformers import AutoModel

            self._model = AutoModel.from_pretrained(
                self.model_name,
                trust_remote_code=True,
                **self._model_kwargs,
            )
            self._model.eval()
            self._model.to(self.device)
        return self._model

    def tokenize(self, text: str) -> list[str]:
        if not text or not text.strip():
            return []
        return self.tokenize_batch([text.strip()])[0]

    def tokenize_batch(self, texts: Sequence[str]) -> list[list[str]]:
        if not texts:
            return []

        results: list[list[str]] = [[] for _ in texts]
        non_empty_indices: list[int] = []
        non_empty_texts: list[str] = []

        for idx, text in enumerate(texts):
            if text and text.strip():
                non_empty_indices.append(idx)
                non_empty_texts.append(text.strip())

        if not non_empty_texts:
            return results

        for start in range(0, len(non_empty_texts), self.batch_size):
            batch_texts = non_empty_texts[start : start + self.batch_size]
            batch_indices = non_empty_indices[start : start + self.batch_size]
            predictions = self.model.predict(
                batch_texts,
                self.tokenizer,
                output_style="json",
            )
            for local_idx, global_idx in enumerate(batch_indices):
                results[global_idx] = self._tokens_from_prediction(predictions[local_idx])

        return results

    def _tokens_from_prediction(self, prediction: Any) -> list[str]:
        if isinstance(prediction, dict) and "tokens" in prediction:
            words = prediction["tokens"]
        else:
            words = prediction

        tokens: list[str] = []
        for word in words:
            segments = word.get("seg")
            if segments:
                tokens.extend(_flatten_segments(segments))
                continue

            token = word.get("token", "")
            if _is_valid_token(token):
                tokens.append(token)
        return tokens

    def analyze(self, text: str) -> list[dict[str, Any]]:
        if not text or not text.strip():
            return []
        return self.analyze_batch([text.strip()])[0]

    def analyze_batch(self, texts: Sequence[str]) -> list[list[dict[str, Any]]]:
        """Per-word morphology: ``{token, core, prefixes, pos, lemma}`` per word.

        ``core`` is the prefix-stripped surface (``seg[-1]``); ``prefixes`` are the
        proclitic segments (``seg[:-1]``). Mirrors ``tokenize_batch`` batching but
        keeps the structured fields instead of flattening to a token list.
        """

        if not texts:
            return []

        results: list[list[dict[str, Any]]] = [[] for _ in texts]
        non_empty_indices: list[int] = []
        non_empty_texts: list[str] = []
        for idx, text in enumerate(texts):
            if text and text.strip():
                non_empty_indices.append(idx)
                non_empty_texts.append(text.strip())

        if not non_empty_texts:
            return results

        for start in range(0, len(non_empty_texts), self.batch_size):
            batch_texts = non_empty_texts[start : start + self.batch_size]
            batch_indices = non_empty_indices[start : start + self.batch_size]
            predictions = self.model.predict(
                batch_texts,
                self.tokenizer,
                output_style="json",
            )
            for local_idx, global_idx in enumerate(batch_indices):
                results[global_idx] = self._words_from_prediction(predictions[local_idx])

        return results

    def _words_from_prediction(self, prediction: Any) -> list[dict[str, Any]]:
        if isinstance(prediction, dict) and "tokens" in prediction:
            words = prediction["tokens"]
            ner = prediction.get("ner_entities") or []
        else:
            words = prediction
            ner = []

        # NER spans index by word position; mark each covered token with its label
        # so a single-word match can demand "name-like in context" evidence.
        ner_label: dict[int, str] = {}
        for ent in ner:
            ts, te = ent.get("token_start"), ent.get("token_end")
            if ts is None or te is None:
                continue
            for ti in range(ts, te + 1):
                ner_label[ti] = ent.get("label")

        out: list[dict[str, Any]] = []
        for ti, word in enumerate(words):
            token = word.get("token", "")
            if not _is_valid_token(token):
                continue
            segments = [s for s in (word.get("seg") or []) if s]
            core = segments[-1] if segments else token
            prefixes = segments[:-1] if len(segments) > 1 else []
            morph = word.get("morph") or {}
            out.append(
                {
                    "token": token,
                    "core": core,
                    "prefixes": prefixes,
                    "pos": morph.get("pos"),
                    "lemma": word.get("lex"),
                    "ner": ner_label.get(ti),
                }
            )
        return out
