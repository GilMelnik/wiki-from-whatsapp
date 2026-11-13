"""dictabert-joint morphology for Hebrew text (moved here from step 1's TF-IDF code).

The GPU is shared with other work on a modest card: ``device=None`` picks CUDA only when at
least ``min_free_gpu_mb`` is free, and a CUDA out-of-memory error halves the batch (down to
one text) and finally moves the model to the CPU instead of failing.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any, Sequence

if TYPE_CHECKING:
    import torch
    from transformers import PreTrainedModel, PreTrainedTokenizerBase

logger = logging.getLogger(__name__)

_SPECIAL_TOKENS = {"[CLS]", "[SEP]"}
_KEEP_TOKEN_RE = re.compile(r"[\u0590-\u05FFa-zA-Z0-9]")


def _is_valid_token(token: str) -> bool:
    if not token or token in _SPECIAL_TOKENS:
        return False
    return _KEEP_TOKEN_RE.search(token) is not None


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


def _resolve_device(device_arg: str | None, min_free_gpu_mb: int) -> "torch.device":
    import torch

    if device_arg is not None:
        return torch.device(device_arg)
    if _cuda_device_is_supported():
        free_mb = torch.cuda.mem_get_info()[0] // (1024 * 1024)
        if free_mb >= min_free_gpu_mb:
            return torch.device("cuda")
        logger.warning(
            f"Only {free_mb} MB free on the GPU (< {min_free_gpu_mb} MB): using the CPU"
        )
    return torch.device("cpu")


class HebrewTokenizer:
    def __init__(
        self,
        model_name: str,
        batch_size: int,
        device: str | None,
        min_free_gpu_mb: int,
    ) -> None:
        self.model_name = model_name
        self.batch_size = batch_size
        self._device_arg = device
        self._min_free_gpu_mb = min_free_gpu_mb
        self._tokenizer = None
        self._model = None
        self._device = None

    @property
    def device(self) -> "torch.device":
        if self._device is None:
            self._device = _resolve_device(self._device_arg, self._min_free_gpu_mb)
        return self._device

    @property
    def tokenizer(self) -> "PreTrainedTokenizerBase":
        if self._tokenizer is None:
            from transformers import AutoTokenizer

            self._tokenizer = AutoTokenizer.from_pretrained(self.model_name)
        return self._tokenizer

    @property
    def model(self) -> "PreTrainedModel":
        if self._model is None:
            from transformers import AutoModel

            self._model = AutoModel.from_pretrained(self.model_name, trust_remote_code=True)
            self._model.eval()
            self._model.to(self.device)
        return self._model

    def _predict(self, texts: Sequence[str]) -> list[Any]:
        """Model predictions in batches, backing off on CUDA out-of-memory errors."""

        import torch

        size = max(1, self.batch_size)
        out: list[Any] = []
        start = 0
        while start < len(texts):
            chunk = list(texts[start : start + size])
            try:
                out.extend(self.model.predict(chunk, self.tokenizer, output_style="json"))
            except torch.cuda.OutOfMemoryError:
                torch.cuda.empty_cache()
                if size > 1:
                    size //= 2
                    logger.warning(f"CUDA out of memory: retrying with batch size {size}")
                elif self.device.type == "cuda":
                    logger.warning("CUDA out of memory at batch size 1: moving to the CPU")
                    self._device = torch.device("cpu")
                    self.model.to(self._device)
                else:
                    raise
                continue
            start += len(chunk)
        return out

    def analyze_batch(self, texts: Sequence[str]) -> list[list[dict[str, Any]]]:
        """Per-word morphology: ``{token, core, prefixes, pos, lemma, ner}`` per word.

        ``core`` is the prefix-stripped surface (``seg[-1]``); ``prefixes`` are the
        proclitic segments (``seg[:-1]``); ``ner`` is the label of the NER span that covers
        the word, if any. An empty text gives no words.
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

        predictions = self._predict(non_empty_texts)
        for global_idx, prediction in zip(non_empty_indices, predictions):
            results[global_idx] = self._words_from_prediction(prediction)

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
