import unittest

from tools.wechat_local_bridge.tgo_protocol import decode_reply


def message(payload, **extra):
    return dict(
        channel_id="visitor-vtr",
        channel_type=251,
        message_seq=4,
        client_msg_no="reply-4",
        from_uid="employee-staff",
        payload=payload,
        **extra,
    )


class TgoProtocolTests(unittest.TestCase):
    def test_final_stream_extracts_main_text_without_thinking_or_tools(self):
        row = message(
            {"type": 100},
            end=1,
            event_meta={
                "completed": True,
                "events": [
                    {
                        "event_key": "thinking",
                        "snapshot": {"kind": "text", "text": "内部推理"},
                    },
                    {
                        "event_key": "main",
                        "snapshot": {"kind": "text", "text": "客户回复\n第二行"},
                    },
                    {
                        "event_key": "tool",
                        "snapshot": {"kind": "tool_call", "text": "内部结果"},
                    },
                ],
            },
        )
        reply = decode_reply(row, "visitor-vtr", "visitor-vtr")
        self.assertEqual(reply.text, "客户回复 第二行")
        self.assertFalse(reply.waiting)

    def test_incomplete_stream_waits_and_foreign_channel_is_rejected(self):
        self.assertTrue(
            decode_reply(
                message({"type": 100}, end=0), "visitor-vtr", "visitor-vtr"
            ).waiting
        )
        with self.assertRaises(ValueError):
            decode_reply(
                message({"type": 1, "content": "秘密"}), "other-vtr", "other-vtr"
            )

    def test_own_inbound_and_failed_or_nontext_replies_are_not_sent(self):
        row = message({"type": 1, "content": "问题"})
        row["from_uid"] = "visitor-vtr"
        self.assertIsNone(decode_reply(row, "visitor-vtr", "visitor-vtr").text)
        self.assertIsNone(
            decode_reply(
                message({"type": 100}, end=1, error="failed"),
                "visitor-vtr",
                "visitor-vtr",
            ).text
        )
        self.assertIsNone(
            decode_reply(
                message({"type": 2, "url": "image"}), "visitor-vtr", "visitor-vtr"
            ).text
        )
