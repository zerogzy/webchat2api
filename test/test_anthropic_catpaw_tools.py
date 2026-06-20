from __future__ import annotations

import json
import os
import re
import inspect
import time
import unittest
from typing import Any
from unittest import mock

from test.optional_stubs import (
    install_curl_cffi_stub,
    install_fastapi_stubs,
    install_pil_stub,
    install_pybase64_stub,
    install_tiktoken_stub,
)

install_curl_cffi_stub()
install_fastapi_stubs()
install_pil_stub()
install_pybase64_stub()
install_tiktoken_stub()

from services.protocol import anthropic_v1_messages
from services.providers.base import CATPAW_PROVIDER, GPT_PROVIDER
from services.providers.catpaw import client as catpaw_client
from services.providers.catpaw import conversation as catpaw_conversation
from services.providers.registry import resolve_model


def _claude_code_tools() -> list[dict[str, Any]]:
    return [
        {
            "name": "Read",
            "description": "Read a file from disk.",
            "input_schema": {
                "type": "object",
                "properties": {"file_path": {"type": "string"}},
                "required": ["file_path"],
            },
        },
        {
            "name": "Write",
            "description": "Write a file to disk.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["file_path", "content"],
            },
        },
        {
            "name": "Edit",
            "description": "Edit a file on disk.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string"},
                    "old_string": {"type": "string"},
                    "new_string": {"type": "string"},
                    "replace_all": {"type": "boolean"},
                },
                "required": ["file_path", "old_string", "new_string"],
            },
        },
        {
            "name": "Bash",
            "description": "Run a shell command.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "command": {"type": "string"},
                    "description": {"type": "string"},
                },
                "required": ["command"],
            },
        },
    ]


class AnthropicCatpawToolTests(unittest.TestCase):
    def setUp(self) -> None:
        os.environ.pop("CATPAW_CLAUDE_ROUTE", None)
        reset = getattr(anthropic_v1_messages, "reset_catpaw_conversation_cache_for_tests", None)
        if callable(reset):
            reset()

    def test_claude_model_routes_to_catpaw_by_default(self) -> None:
        self.assertEqual(resolve_model("claude-sonnet-4-20250514").provider, CATPAW_PROVIDER)
        self.assertEqual(resolve_model("claude-haiku-4-20250514").provider, CATPAW_PROVIDER)

    def test_claude_model_route_can_be_disabled(self) -> None:
        with mock.patch.dict(os.environ, {"CATPAW_CLAUDE_ROUTE": "0"}):
            self.assertEqual(resolve_model("claude-sonnet-4-20250514").provider, GPT_PROVIDER)

    def test_message_request_for_claude_model_does_not_require_gpt_token(self) -> None:
        with mock.patch.object(
            anthropic_v1_messages.account_service,
            "get_text_access_token",
            side_effect=AssertionError("claude model should route to CatPaw"),
        ):
            request = anthropic_v1_messages.message_request(
                {
                    "model": "claude-sonnet-4-20250514",
                    "messages": [{"role": "user", "content": "Read README.md"}],
                    "tools": _claude_code_tools(),
                }
            )

        self.assertTrue(request.catpaw_mode)
        self.assertIsNone(request.backend)
        self.assertEqual(request.model, "claude-sonnet-4-20250514")
        self.assertIn("AVAILABLE TOOLS", request.messages[0]["content"])

    def test_claude_code_tool_followup_reuses_catpaw_conversation_id(self) -> None:
        tools = _claude_code_tools()
        first = anthropic_v1_messages.message_request(
            {
                "model": "claude-sonnet-4-20250514",
                "messages": [{"role": "user", "content": "Read README.md"}],
                "tools": tools,
            }
        )
        second = anthropic_v1_messages.message_request(
            {
                "model": "claude-sonnet-4-20250514",
                "messages": [
                    {"role": "user", "content": "Read README.md"},
                    {
                        "role": "assistant",
                        "content": [
                            {
                                "type": "tool_use",
                                "id": "toolu_read",
                                "name": "Read",
                                "input": {"file_path": "README.md"},
                            }
                        ],
                    },
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": "toolu_read",
                                "content": [{"type": "text", "text": "# webchat2api"}],
                            }
                        ],
                    },
                ],
                "tools": tools,
            }
        )

        self.assertRegex(
            getattr(first, "catpaw_conversation_id", ""),
            re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"),
        )
        self.assertEqual(first.catpaw_conversation_id, second.catpaw_conversation_id)

    def test_different_claude_code_roots_get_different_catpaw_conversation_ids(self) -> None:
        first = anthropic_v1_messages.message_request(
            {
                "model": "claude-sonnet-4-20250514",
                "messages": [{"role": "user", "content": "Read README.md"}],
                "tools": _claude_code_tools(),
            }
        )
        second = anthropic_v1_messages.message_request(
            {
                "model": "claude-sonnet-4-20250514",
                "messages": [{"role": "user", "content": "List docs files"}],
                "tools": _claude_code_tools(),
            }
        )

        self.assertNotEqual(first.catpaw_conversation_id, second.catpaw_conversation_id)

    def test_same_claude_code_root_with_different_model_gets_different_catpaw_conversation_id(self) -> None:
        first = anthropic_v1_messages.message_request(
            {
                "model": "claude-sonnet-4-20250514",
                "messages": [{"role": "user", "content": "Read README.md"}],
                "tools": _claude_code_tools(),
            }
        )
        second = anthropic_v1_messages.message_request(
            {
                "model": "claude-haiku-4-20250514",
                "messages": [{"role": "user", "content": "Read README.md"}],
                "tools": _claude_code_tools(),
            }
        )

        self.assertNotEqual(first.catpaw_conversation_id, second.catpaw_conversation_id)

    def test_same_claude_code_root_with_different_tools_gets_different_catpaw_conversation_id(self) -> None:
        first = anthropic_v1_messages.message_request(
            {
                "model": "claude-sonnet-4-20250514",
                "messages": [{"role": "user", "content": "Read README.md"}],
                "tools": _claude_code_tools(),
            }
        )
        second = anthropic_v1_messages.message_request(
            {
                "model": "claude-sonnet-4-20250514",
                "messages": [{"role": "user", "content": "Read README.md"}],
                "tools": [_claude_code_tools()[0]],
            }
        )

        self.assertNotEqual(first.catpaw_conversation_id, second.catpaw_conversation_id)

    def test_same_claude_code_root_with_different_system_gets_different_catpaw_conversation_id(self) -> None:
        first = anthropic_v1_messages.message_request(
            {
                "model": "claude-sonnet-4-20250514",
                "system": "You are Claude Code in project A.",
                "messages": [{"role": "user", "content": "Read README.md"}],
                "tools": _claude_code_tools(),
            }
        )
        second = anthropic_v1_messages.message_request(
            {
                "model": "claude-sonnet-4-20250514",
                "system": "You are Claude Code in project B.",
                "messages": [{"role": "user", "content": "Read README.md"}],
                "tools": _claude_code_tools(),
            }
        )

        self.assertNotEqual(first.catpaw_conversation_id, second.catpaw_conversation_id)

    def test_same_claude_code_root_with_different_session_header_gets_different_catpaw_conversation_id(self) -> None:
        first = anthropic_v1_messages.message_request(
            {
                "model": "claude-sonnet-4-20250514",
                "_request_headers": {"x-claude-code-session-id": "session-a"},
                "messages": [{"role": "user", "content": "Read README.md"}],
                "tools": _claude_code_tools(),
            }
        )
        second = anthropic_v1_messages.message_request(
            {
                "model": "claude-sonnet-4-20250514",
                "_request_headers": {"x-claude-code-session-id": "session-b"},
                "messages": [
                    {"role": "user", "content": "Read README.md"},
                    {"role": "assistant", "content": "ok"},
                    {"role": "user", "content": "Continue"},
                ],
                "tools": _claude_code_tools(),
            }
        )

        self.assertNotEqual(first.catpaw_conversation_id, second.catpaw_conversation_id)

    def test_anthropic_handle_passes_catpaw_conversation_id_to_client(self) -> None:
        captured: dict[str, Any] = {}

        def fake_stream_chat_deltas(*args: Any, **kwargs: Any):
            captured["conversation_id"] = kwargs.get("conversation_id")
            yield "ok"

        with mock.patch.object(catpaw_client, "stream_chat_deltas", side_effect=fake_stream_chat_deltas):
            response = anthropic_v1_messages.handle(
                {
                    "model": "claude-sonnet-4-20250514",
                    "messages": [{"role": "user", "content": "Read README.md"}],
                    "tools": _claude_code_tools(),
                }
            )

        self.assertEqual(response["content"], [{"type": "text", "text": "ok"}])
        self.assertRegex(
            captured.get("conversation_id", ""),
            re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"),
        )

    def test_catpaw_conversation_bootstrap_expires_before_followup(self) -> None:
        cache = catpaw_conversation.CatpawConversationCache(
            bootstrap_ttl_seconds=1,
            active_ttl_seconds=100,
        )
        first = cache.conversation_id("root-a", "branch-a", has_history=False, now=0)
        second = cache.conversation_id("root-a", "branch-a", has_history=True, now=2)

        self.assertNotEqual(first, second)

    def test_catpaw_conversation_active_entry_reuses_within_ttl(self) -> None:
        cache = catpaw_conversation.CatpawConversationCache(
            bootstrap_ttl_seconds=10,
            active_ttl_seconds=100,
        )
        first = cache.conversation_id("root-a", "branch-a", has_history=False, now=0)
        second = cache.conversation_id("root-a", "branch-a", has_history=True, now=1)
        third = cache.conversation_id("root-a", "branch-a", has_history=True, now=50)

        self.assertEqual(first, second)
        self.assertEqual(second, third)

    def test_catpaw_text_tool_calls_convert_to_claude_tool_use_for_file_operations(self) -> None:
        windows_path = r"C:\Users\zero\Desktop\美团ai\webchat2api\README.md"
        cases = [
            (
                "Read",
                '<tool_calls><tool_name>Read</tool_name><parameters>{"file_path":"'
                + windows_path
                + '"}</parameters></tool_calls>',
                {"file_path": windows_path},
            ),
            (
                "Write",
                '<tool_calls><tool_name>Write</tool_name><parameters>{"file_path":"C:/tmp/catpaw.txt","content":"hello\\nworld"}</parameters></tool_calls>',
                {"file_path": "C:/tmp/catpaw.txt", "content": "hello\nworld"},
            ),
            (
                "Edit",
                '<tool_calls><tool_name>Edit</tool_name><parameters>{"file_path":"C:/tmp/catpaw.txt","old_string":"hello","new_string":"hello from edit","replace_all":false}</parameters></tool_calls>',
                {
                    "file_path": "C:/tmp/catpaw.txt",
                    "old_string": "hello",
                    "new_string": "hello from edit",
                    "replace_all": False,
                },
            ),
            (
                "Bash",
                '<tool_calls><tool_name>Bash</tool_name><parameters>{"command":"rm -f C:/tmp/catpaw.txt","description":"Delete catpaw test file"}</parameters></tool_calls>',
                {"command": "rm -f C:/tmp/catpaw.txt", "description": "Delete catpaw test file"},
            ),
        ]

        for name, text, expected_input in cases:
            with self.subTest(name=name):
                response = anthropic_v1_messages.message_response(
                    "claude-sonnet-4-20250514",
                    text,
                    input_tokens=10,
                    output_tokens=5,
                    tools=_claude_code_tools(),
                )

                json.dumps(response)
                self.assertEqual(response["stop_reason"], "tool_use")
                block = response["content"][-1]
                self.assertEqual(block["type"], "tool_use")
                self.assertTrue(str(block["id"]).startswith("toolu_"))
                self.assertEqual(block["name"], name)
                self.assertEqual(block["input"], expected_input)

    def test_bash_tool_windows_paths_convert_to_posix_shell_paths(self) -> None:
        response = anthropic_v1_messages.message_response(
            "glm-5.1",
            r'<tool_calls><tool_name>Bash</tool_name><parameters>{"command":"ls C:\Users\zero\Desktop","description":"List Desktop"}</parameters></tool_calls>',
            input_tokens=10,
            output_tokens=5,
            tools=_claude_code_tools(),
        )

        block = response["content"][-1]
        self.assertEqual(block["type"], "tool_use")
        self.assertEqual(block["name"], "Bash")
        self.assertEqual(block["input"], {"command": "ls /c/Users/zero/Desktop", "description": "List Desktop"})

    def test_malformed_catpaw_glob_call_maps_to_bash_when_glob_is_unavailable(self) -> None:
        response = anthropic_v1_messages.message_response(
            "glm-5.1",
            '我来帮您查看桌面文件夹中关于 frp 的文件。tool_call>Glob{"pattern":"~/Desktop/*frp*"}',
            input_tokens=10,
            output_tokens=5,
            tools=_claude_code_tools(),
        )

        block = response["content"][-1]
        self.assertEqual(response["stop_reason"], "tool_use")
        self.assertEqual(block["type"], "tool_use")
        self.assertEqual(block["name"], "Bash")
        self.assertEqual(
            block["input"],
            {
                "command": "find ~/Desktop -maxdepth 1 -iname '*frp*' -print",
                "description": "Find files matching ~/Desktop/*frp*",
            },
        )

    def test_catpaw_tool_name_xml_elements_convert_to_claude_tool_use(self) -> None:
        response = anthropic_v1_messages.message_response(
            "glm-5.1",
            "<tool_calls>"
            "<Write><file_path>C:/tmp/catpaw.txt</file_path><content>catpaw-ok</content></Write>"
            "<Read><file_path>C:/tmp/catpaw.txt</file_path></Read>"
            '<Bash><command>rm "C:/tmp/catpaw.txt"</command><description>Delete verification file</description></Bash>'
            "</tool_calls>",
            input_tokens=10,
            output_tokens=5,
            tools=_claude_code_tools(),
        )

        blocks = [block for block in response["content"] if block["type"] == "tool_use"]
        self.assertEqual(response["stop_reason"], "tool_use")
        self.assertEqual(
            [(block["name"], block["input"]) for block in blocks],
            [
                ("Write", {"file_path": "C:/tmp/catpaw.txt", "content": "catpaw-ok"}),
                ("Read", {"file_path": "C:/tmp/catpaw.txt"}),
                ("Bash", {"command": 'rm "C:/tmp/catpaw.txt"', "description": "Delete verification file"}),
            ],
        )

    def test_nested_parameters_object_is_flattened_for_claude_tools(self) -> None:
        response = anthropic_v1_messages.message_response(
            "glm-5.1",
            'tool_call>Write{"parameters":{"file_path":"C:/tmp/catpaw.txt","content":"catpaw-ok"}}',
            input_tokens=10,
            output_tokens=5,
            tools=_claude_code_tools(),
        )

        block = response["content"][-1]
        self.assertEqual(response["stop_reason"], "tool_use")
        self.assertEqual(block["name"], "Write")
        self.assertEqual(block["input"], {"file_path": "C:/tmp/catpaw.txt", "content": "catpaw-ok"})

    def test_anthropic_stream_buffers_catpaw_tool_xml_into_input_json_delta(self) -> None:
        text = (
            '<tool_calls><tool_name>Write</tool_name><parameters>'
            '{"file_path":"C:/tmp/catpaw-stream.txt","content":"stream ok"}'
            "</parameters></tool_calls>"
        )
        chunks = [
            {"choices": [{"delta": {"role": "assistant", "content": text[:18]}, "finish_reason": None}]},
            {"choices": [{"delta": {"content": text[18:80]}, "finish_reason": None}]},
            {"choices": [{"delta": {"content": text[80:]}, "finish_reason": None}]},
            {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
        ]

        events = list(
            anthropic_v1_messages.stream_events(
                chunks,
                "claude-sonnet-4-20250514",
                input_tokens=10,
                output_tokens=lambda _: 5,
                tools=_claude_code_tools(),
            )
        )

        text_deltas = [
            event["delta"]["text"]
            for event in events
            if event.get("type") == "content_block_delta" and event.get("delta", {}).get("type") == "text_delta"
        ]
        self.assertFalse(any("<tool" in text for text in text_deltas))
        tool_start = next(
            event
            for event in events
            if event.get("type") == "content_block_start"
            and event.get("content_block", {}).get("type") == "tool_use"
        )
        self.assertEqual(tool_start["content_block"]["name"], "Write")
        json_delta = next(
            event
            for event in events
            if event.get("type") == "content_block_delta"
            and event.get("delta", {}).get("type") == "input_json_delta"
        )
        self.assertEqual(
            json.loads(json_delta["delta"]["partial_json"]),
            {"file_path": "C:/tmp/catpaw-stream.txt", "content": "stream ok"},
        )
        message_delta = next(event for event in events if event.get("type") == "message_delta")
        self.assertEqual(message_delta["delta"]["stop_reason"], "tool_use")

    def test_anthropic_stream_sends_ping_while_waiting_for_upstream_chunks(self) -> None:
        def slow_chunks():
            time.sleep(0.05)
            yield {"choices": [{"delta": {"role": "assistant", "content": "ok"}, "finish_reason": None}]}
            yield {"choices": [{"delta": {}, "finish_reason": "stop"}]}

        with mock.patch.object(anthropic_v1_messages, "STREAM_PING_INTERVAL_SECONDS", 0.01, create=True):
            events = list(
                anthropic_v1_messages.stream_events(
                    slow_chunks(),
                    "claude-sonnet-4-20250514",
                    input_tokens=10,
                    output_tokens=lambda _: 1,
                )
            )

        self.assertIn("ping", [event.get("type") for event in events])

    def test_claude_tool_results_are_forwarded_to_catpaw_as_text_history(self) -> None:
        with mock.patch.object(
            anthropic_v1_messages.account_service,
            "get_text_access_token",
            side_effect=AssertionError("claude model should route to CatPaw"),
        ):
            request = anthropic_v1_messages.message_request(
                {
                    "model": "claude-sonnet-4-20250514",
                    "messages": [
                        {
                            "role": "assistant",
                            "content": [
                                {
                                    "type": "tool_use",
                                    "id": "toolu_read",
                                    "name": "Read",
                                    "input": {"file_path": "C:/tmp/catpaw.txt"},
                                }
                            ],
                        },
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "tool_result",
                                    "tool_use_id": "toolu_read",
                                    "content": [{"type": "text", "text": "hello from file"}],
                                }
                            ],
                        },
                    ],
                    "tools": _claude_code_tools(),
                }
            )

        catpaw_messages = catpaw_client._to_catpaw_messages(request.messages)

        self.assertIn("<tool_calls><tool_name>Read</tool_name>", catpaw_messages[-2]["content"])
        self.assertIn('"file_path": "C:/tmp/catpaw.txt"', catpaw_messages[-2]["content"])
        self.assertEqual(catpaw_messages[-1]["role"], "user")
        self.assertIn("Tool result (id=toolu_read):\nhello from file", catpaw_messages[-1]["content"])

    def test_catpaw_stream_body_disables_official_agent_auto_mode(self) -> None:
        body = catpaw_client._build_stream_body(
            [{"role": "user", "content": "hello"}],
            59,
        )

        self.assertRegex(
            body["conversationId"],
            re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"),
        )
        self.assertEqual(body["userModelTypeCode"], 59)
        self.assertEqual(body["chatApplyModeType"], "chat")
        self.assertEqual(
            body["agentModeConfig"],
            {"model": {"default": 59, "maxMode": True, "autoMode": False}},
        )
        self.assertNotIn("enableAutoMode", body)
        self.assertNotIn("selectedModelType", body)
        self.assertNotIn("selectedModelName", body)

    def test_catpaw_stream_body_accepts_supplied_conversation_id(self) -> None:
        self.assertIn("conversation_id", inspect.signature(catpaw_client._build_stream_body).parameters)
        fixed_id = "11111111-2222-4333-8444-555555555555"
        body = catpaw_client._build_stream_body(
            [{"role": "user", "content": "hello"}],
            59,
            conversation_id=fixed_id,
        )

        self.assertEqual(body["conversationId"], fixed_id)


if __name__ == "__main__":
    unittest.main()
