"""Which knowledge label holds for a conversation, and which conversations need a new one.

One rule, used by step 2's run and by the conversations page of the review tool: a label
belongs to the conversation *as it was when the label was decided*, whoever decided it.

- Step 2's own record (``threads_classified.json``) holds while the conversation still gives
  the input fingerprint stored for it (``metadata.inputs[thread_id]``): its messages plus the
  decision settings (classifier, question, threshold, heuristic).
- A label the owner set by hand is a record of the edited copy
  (``threads_classified_edited.json``) carrying ``manual_fingerprint``: the fingerprint of the
  conversation's messages alone at the moment of labelling. It holds while the messages are
  the same, whatever the settings do (a new threshold or question does not undo the owner's
  work). The field's presence means "set by hand", also when the label says what step 2 said.
  A conversation step 2 never answered gets ``{thread_id, passed_heuristic: false,
  p_knowledge: null, is_knowledge_bearing, manual_fingerprint}``.
- An older hand label (an edited record that differs from step 2's own and has no
  ``manual_fingerprint``; only dev data has them) holds exactly while step 2's own record does
  under the settings stored with it; step 2's next run gives it its ``manual_fingerprint``.
- A conversation *needs a label* when no label is in force for it, or the one in force no
  longer holds. Step 2's next normal run asks about exactly those; nothing else is asked.

Only ``messages[].{id, datetime, sender, content}`` are read (``MESSAGE_FIELDS``): a new
reaction changes nothing. Nothing here builds a classifier or calls a model.
"""

from __future__ import annotations

from typing import Any

from utils.reconcile import fingerprint

MESSAGE_FIELDS = ("id", "datetime", "sender", "content")
SETTINGS_KEYS = ("classifier", "question", "threshold", "heuristic")
MANUAL_FINGERPRINT = "manual_fingerprint"


def _message_rows(thread: dict[str, Any]) -> list[list[Any]]:
    return [[message.get(key) for key in MESSAGE_FIELDS] for message in thread["messages"]]


def content_fingerprint(thread: dict[str, Any]) -> str:
    """Fingerprint of what step 2 reads of the conversation's messages, without any setting."""
    return fingerprint({"messages": _message_rows(thread)})


def input_fingerprint(thread: dict[str, Any], settings: dict[str, Any]) -> str:
    """Step 2's input fingerprint of the conversation: its messages plus the decision settings
    (``ThreadClassifier.settings()``, or the ones stored in the step's metadata)."""
    return fingerprint({"messages": _message_rows(thread), **settings})


def hand_record(
    thread: dict[str, Any], value: bool, own: dict[str, Any] | None
) -> dict[str, Any]:
    """The owner's label ``value`` for the conversation as it is now; step 2's own answer
    (``own``, when there is one) keeps its probability next to it."""
    return {
        "thread_id": thread["thread_id"],
        "passed_heuristic": bool(own["passed_heuristic"]) if own else False,
        "p_knowledge": own["p_knowledge"] if own else None,
        "is_knowledge_bearing": value,
        MANUAL_FINGERPRINT: content_fingerprint(thread),
    }


class LabelRule:
    """The labels of step 2's two files, judged against the conversations as they are now.

    ``original`` / ``edited`` are the two files' payloads (``None`` when a file does not exist).
    ``settings`` decide whether step 2's own records still hold: by default the ones stored in
    the original's metadata (what the page uses: a changed ``config.json`` alone makes nothing
    need a label); step 2's run passes its current settings, so it also re-asks after a
    settings change, except where the owner's label holds.
    """

    def __init__(
        self,
        original: dict[str, Any] | None,
        edited: dict[str, Any] | None,
        settings: dict[str, Any] | None = None,
    ) -> None:
        metadata = (original or {}).get("metadata", {})
        self.own = {record["thread_id"]: record for record in (original or {}).get("threads", [])}
        self.inputs: dict[str, str] = metadata.get("inputs", {})
        self.stored_settings = {key: metadata.get(key) for key in SETTINGS_KEYS}
        self.settings = self.stored_settings if settings is None else settings
        self.in_force = (
            self.own
            if edited is None
            else {record["thread_id"]: record for record in edited.get("threads", [])}
        )

    def own_holds(self, thread: dict[str, Any], settings: dict[str, Any] | None = None) -> bool:
        """Step 2 answered the conversation as it is now, under ``settings`` (default: the
        rule's)."""
        thread_id = thread["thread_id"]
        stored = self.inputs.get(thread_id)
        return (
            thread_id in self.own
            and stored is not None
            and stored == input_fingerprint(thread, settings or self.settings)
        )

    def by_hand(self, thread_id: str) -> bool:
        """The label in force was set by the owner (whether or not it still holds)."""
        record = self.in_force.get(thread_id)
        if record is None:
            return False
        own = self.own.get(thread_id)
        return MANUAL_FINGERPRINT in record or (own is not None and record != own)

    def hand_holds(self, thread: dict[str, Any]) -> bool:
        """The owner labelled the conversation as it is now."""
        thread_id = thread["thread_id"]
        if not self.by_hand(thread_id):
            return False
        record = self.in_force[thread_id]
        if MANUAL_FINGERPRINT in record:
            return record[MANUAL_FINGERPRINT] == content_fingerprint(thread)
        # An older hand label: judged by the settings it was given under, like the page does.
        return self.own_holds(thread, self.stored_settings)

    def needs_label(self, thread: dict[str, Any]) -> bool:
        """No label is in force, or the one in force was decided on other messages."""
        thread_id = thread["thread_id"]
        if thread_id not in self.in_force:
            return True
        if self.by_hand(thread_id):
            return not self.hand_holds(thread)
        return not self.own_holds(thread)
