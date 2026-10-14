from __future__ import annotations

import pathlib
import sys
import unittest


TOOLS = pathlib.Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))

from venice_duplicate_review import assess, chat_signature  # noqa: E402


def record(messages):
    return {
        "conversationId": "same-id",
        "contentSha256": chat_signature({"messages": messages, "messageImages": []})["contentSha256"],
        "wordCount": 20,
        **chat_signature({"messages": messages, "messageImages": []}),
    }


class ConversationRevisionTests(unittest.TestCase):
    def test_exact_prefix_is_a_strict_extension(self):
        old = record([{"id": "1", "role": "user", "content": "start"}])
        new = record([
            {"id": "1", "role": "user", "content": "start"},
            {"id": "2", "role": "assistant", "content": "continued"},
        ])
        self.assertEqual(assess(old, new)[0], "same-id-strict-extension")
        self.assertEqual(assess(new, old)[0], "same-id-strict-prefix")

    def test_changed_prior_message_is_not_append(self):
        old = record([{"id": "1", "role": "user", "content": "start"}])
        edited = record([
            {"id": "1", "role": "user", "content": "edited"},
            {"id": "2", "role": "assistant", "content": "continued"},
        ])
        self.assertEqual(assess(old, edited)[0], "same-id-edited-or-branched")


if __name__ == "__main__":
    unittest.main()
