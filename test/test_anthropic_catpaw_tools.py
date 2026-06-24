from __future__ import annotations

import json
import os
import re
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
from services.providers.registry import resolve_model


def _claude_code_tools() -> list[dict[str, Any]]:
    return [
        {
            "name": "Read",
            "description": "Read a file from disk.",
            "input_schema": {"type": "object", "properties": {"file_path": {"type": "string"}}, "required": ["file_path"]},
        },
        {
            "name": "Write",
            "description": "Write a file to disk.",
            "input_schema": {
                "type": "object",
                "properties": {"file_path": {"type": "string"}, "content": {"type": "string"}},
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
                "properties": {"command": {"type": "string"}, "description": {"type": "string"}},
                "required": ["command"],
            },
        },
        {
            "name": "Glob",
            "description": "Find files by glob.",
            "input_schema": {
                "type": "object",
                "properties": {"pattern": {"type": "string"}},
                "required": ["pattern"],
            },
        },
    ]


class AnthropicCatpawToolTests(unittest.TestCase):
    def setUp(self) -> None:
        os.environ.pop("CATPAW_CLAUDE_ROUTE", None)
        anthropic_v1_messages.reset_catpaw_conversation_cache_for_tests()

    def test_claude_model_routes_to_catpaw_by_default(self) -> None:
        self.assertEqual(resolve_model("claude-sonnet-4-20250514").provider, CATPAW_PROVIDER)
        self.assertEqual(resolve_model("claude-haiku-4-20250514").provider, CATPAW_PROVIDER)

    def test_claude_model_route_can_be_disabled(self) -> None:
        with mock.patch.dict(os.environ, {"CATPAW_CLAUDE_ROUTE": "0"}):
            self.assertEqual(resolve_model("claude-sonnet-4-20250514").provider, GPT_PROVIDER)

    def test_anthropic_request_translates_tools_and_tool_results_to_openai_body(self) -> None:
        body = anthropic_v1_messages.anthropic_to_openai_body(
            {
                "model": "claude-sonnet-4-20250514",
                "system": [{"type": "text", "text": "You are Claude Code."}],
                "messages": [
                    {"role": "user", "content": "Read README.md"},
                    {
                        "role": "assistant",
                        "content": [
                            {"type": "text", "text": "I will read it."},
                            {"type": "tool_use", "id": "toolu_read", "name": "Read", "input": {"file_path": "README.md"}},
                        ],
                    },
                    {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_read", "content": [{"type": "text", "text": "# webchat2api"}]}]},
                ],
                "tools": _claude_code_tools(),
                "tool_choice": {"type": "tool", "name": "Read"},
            }
        )

        self.assertEqual(body["model"], "claude-sonnet-4-20250514")
        self.assertEqual(body["messages"][0], {"role": "system", "content": "You are Claude Code."})
        self.assertEqual(body["messages"][2]["tool_calls"][0]["id"], "toolu_read")
        self.assertEqual(body["messages"][3], {"role": "tool", "tool_call_id": "toolu_read", "content": "# webchat2api"})
        self.assertEqual(body["tools"][0]["function"]["name"], "Read")
        self.assertEqual(body["tool_choice"], {"type": "function", "function": {"name": "Read"}})

    def test_anthropic_response_translates_openai_tool_calls_to_tool_use(self) -> None:
        response = anthropic_v1_messages.message_response_from_openai(
            {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call_read",
                                    "type": "function",
                                    "function": {"name": "Read", "arguments": '{"file_path":"README.md"}'},
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            },
            "claude-sonnet-4-20250514",
        )

        self.assertEqual(response["stop_reason"], "tool_use")
        self.assertEqual(response["content"], [{"type": "tool_use", "id": "call_read", "name": "Read", "input": {"file_path": "README.md"}}])
        self.assertEqual(response["usage"], {"input_tokens": 10, "output_tokens": 5})

    def test_anthropic_handle_uses_catpaw_without_gpt_token(self) -> None:
        with (
            mock.patch.object(anthropic_v1_messages.openai_v1_chat_complete.catpaw_chat, "chat_completion", return_value="<tool_calls><tool_name>Read</tool_name><parameters>{\"file_path\":\"README.md\"}</parameters></tool_calls>"),
            mock.patch.object(
                anthropic_v1_messages.openai_v1_chat_complete,
                "text_backend",
                side_effect=AssertionError("claude model should route to CatPaw"),
            ),
        ):
            response = anthropic_v1_messages.handle(
                {
                    "model": "claude-sonnet-4-20250514",
                    "messages": [{"role": "user", "content": "Read README.md"}],
                    "tools": _claude_code_tools(),
                }
            )

        self.assertEqual(response["stop_reason"], "tool_use")
        self.assertEqual(response["content"][-1]["name"], "Read")
        self.assertEqual(response["content"][-1]["input"], {"file_path": "README.md"})

    def test_anthropic_catpaw_reuses_conversation_id_for_same_claude_code_session(self) -> None:
        first = anthropic_v1_messages.anthropic_to_openai_body(
            {
                "model": "claude-sonnet-4-20250514",
                "_request_headers": {"x-claude-code-session-id": "session-a"},
                "messages": [{"role": "user", "content": "Read README.md"}],
                "tools": _claude_code_tools(),
            }
        )
        followup = anthropic_v1_messages.anthropic_to_openai_body(
            {
                "model": "claude-sonnet-4-20250514",
                "_request_headers": {"x-claude-code-session-id": "session-a"},
                "messages": [
                    {"role": "user", "content": "Read README.md"},
                    {
                        "role": "assistant",
                        "content": [{"type": "tool_use", "id": "toolu_read", "name": "Read", "input": {"file_path": "README.md"}}],
                    },
                    {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_read", "content": "ok"}]},
                ],
                "tools": _claude_code_tools(),
            }
        )

        self.assertRegex(first["catpaw_conversation_id"], re.compile(r"^[0-9a-f-]{36}$"))
        self.assertEqual(followup["catpaw_conversation_id"], first["catpaw_conversation_id"])

    def test_anthropic_catpaw_conversation_id_isolated_by_claude_code_session(self) -> None:
        base = {
            "model": "claude-sonnet-4-20250514",
            "messages": [{"role": "user", "content": "Read README.md"}],
            "tools": _claude_code_tools(),
        }
        first = anthropic_v1_messages.anthropic_to_openai_body({**base, "_request_headers": {"x-claude-code-session-id": "session-a"}})
        second = anthropic_v1_messages.anthropic_to_openai_body({**base, "_request_headers": {"x-claude-code-session-id": "session-b"}})

        self.assertNotEqual(second["catpaw_conversation_id"], first["catpaw_conversation_id"])

    def test_anthropic_handle_passes_conversation_id_to_catpaw_adapter(self) -> None:
        captured: list[dict[str, Any]] = []

        def fake_chat_completion(body: dict[str, Any], messages: list[dict[str, Any]], model: str, backend: Any = None) -> str:
            captured.append(dict(body))
            return "ok"

        with mock.patch.object(anthropic_v1_messages.openai_v1_chat_complete.catpaw_chat, "chat_completion", side_effect=fake_chat_completion):
            response = anthropic_v1_messages.handle(
                {
                    "model": "claude-sonnet-4-20250514",
                    "_request_headers": {"x-claude-code-session-id": "session-a"},
                    "messages": [{"role": "user", "content": "hello"}],
                }
            )

        self.assertEqual(response["content"], [{"type": "text", "text": "ok"}])
        self.assertRegex(captured[0]["catpaw_conversation_id"], re.compile(r"^[0-9a-f-]{36}$"))

    def test_tool_parser_keeps_catpaw_file_operations_compatible(self) -> None:
        cases = [
            ('<tool_calls><tool_name>Read</tool_name><parameters>{"file_path":"README.md"}</parameters></tool_calls>', "Read", {"file_path": "README.md"}),
            ('<tool_calls><Write><file_path>C:/tmp/catpaw.txt</file_path><content>ok</content></Write></tool_calls>', "Write", {"file_path": "C:/tmp/catpaw.txt", "content": "ok"}),
            ('tool_call>Write{"parameters":{"file_path":"C:/tmp/catpaw.txt","content":"ok"}}', "Write", {"file_path": "C:/tmp/catpaw.txt", "content": "ok"}),
            (r'<tool_calls><tool_name>Bash</tool_name><parameters>{"command":"ls C:\Users\zero\Desktop","description":"List"}</parameters></tool_calls>', "Bash", {"command": "ls /c/Users/zero/Desktop", "description": "List"}),
            ('我来查看文件。tool_call>Glob{"pattern":"~/Desktop/*frp*"}', "Glob", {"pattern": "~/Desktop/*frp*"}),
            ('tool_call>Bash{"cmd":"pwd"}', "Bash", {"command": "pwd"}),
            ('tool_call>Bash{"shell_command":"ls -la"}', "Bash", {"command": "ls -la"}),
            ('<tool_calls><Bash>ls -la</Bash></tool_calls>', "Bash", {"command": "ls -la"}),
            ('Bash({"command":" \\"mkdir -p /home/claude/api\\"","description":"创建api目录"})', "Bash", {"command": "mkdir -p /home/claude/api", "description": "创建api目录"}),
            ('Bash(mkdir -p /home/claude/api)', "Bash", {"command": "mkdir -p /home/claude/api"}),
            ('<tool_call>Bash<command>mkdir -p /home/claude/api</command><description>创建api目录</description></Bash>', "Bash", {"command": "mkdir -p /home/claude/api", "description": "创建api目录"}),
            ('<tool_call>Write<file_path>/home/claude/api/calc.py</file_path><content>print(1)</content></Write>', "Write", {"file_path": "/home/claude/api/calc.py", "content": "print(1)"}),
            ('tool_call>Bash{"command":"{\\"command\\":\\" \\"mkdir -p /home/claude/api\\"\\",\\"description\\":\\"创建api目录\\"}"}', "Bash", {"command": "mkdir -p /home/claude/api"}),
            ('tool_call>Bash{"command":" "mkdir -p /home/claude/api"","description":"创建api目录"}', "Bash", {"command": "mkdir -p /home/claude/api"}),
        ]
        tools = _claude_code_tools()

        for text, name, expected in cases:
            with self.subTest(name=name):
                parsed = anthropic_v1_messages.tool_calls.parse_tool_calls_for_tools(text, tools)
                self.assertEqual(len(parsed.calls), 1)
                self.assertEqual(parsed.calls[0].name, name)
                self.assertEqual(json.loads(parsed.calls[0].arguments), expected)

    def test_tool_parser_does_not_emit_unrecoverable_invalid_bash(self) -> None:
        tools = _claude_code_tools()

        parsed = anthropic_v1_messages.tool_calls.parse_tool_calls_for_tools('tool_call>Bash{"description":"list files"}', tools)

        self.assertEqual(parsed.calls, [])

    def test_glob_falls_back_to_bash_only_when_glob_tool_is_unavailable(self) -> None:
        tools = [tool for tool in _claude_code_tools() if tool["name"] != "Glob"]

        parsed = anthropic_v1_messages.tool_calls.parse_tool_calls_for_tools('tool_call>Glob{"pattern":"~/Desktop/*frp*"}', tools)

        self.assertEqual(len(parsed.calls), 1)
        self.assertEqual(parsed.calls[0].name, "Bash")
        self.assertEqual(
            json.loads(parsed.calls[0].arguments),
            {"command": "find ~/Desktop -maxdepth 1 -iname '*frp*' -print", "description": "Find files matching ~/Desktop/*frp*"},
        )

    def test_anthropic_stream_translates_openai_tool_chunks_to_input_json_delta(self) -> None:
        chunks = [
            {"choices": [{"delta": {"role": "assistant"}, "finish_reason": None}]},
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "call_write", "type": "function", "function": {"name": "Write", "arguments": ""}}]}, "finish_reason": None}]},
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": '{"file_path":"C:/tmp/a.txt"'}}]}, "finish_reason": None}]},
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": ',"content":"ok"}'}}]}, "finish_reason": None}]},
            {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
        ]

        events = list(anthropic_v1_messages.stream_events_from_openai(chunks, "claude-sonnet-4-20250514", 10))

        start = next(event for event in events if event.get("type") == "content_block_start" and event.get("content_block", {}).get("type") == "tool_use")
        self.assertEqual(start["content_block"]["name"], "Write")
        delta = next(event for event in events if event.get("type") == "content_block_delta" and event.get("delta", {}).get("type") == "input_json_delta")
        self.assertEqual(json.loads(delta["delta"]["partial_json"]), {"file_path": "C:/tmp/a.txt", "content": "ok"})
        message_delta = next(event for event in events if event.get("type") == "message_delta")
        self.assertEqual(message_delta["delta"]["stop_reason"], "tool_use")

    def test_anthropic_stream_sends_ping_while_waiting_for_upstream_chunks(self) -> None:
        def slow_chunks():
            time.sleep(0.05)
            yield {"choices": [{"delta": {"role": "assistant", "content": "ok"}, "finish_reason": None}]}
            yield {"choices": [{"delta": {}, "finish_reason": "stop"}]}

        with mock.patch.object(anthropic_v1_messages, "STREAM_PING_INTERVAL_SECONDS", 0.01, create=True):
            events = list(anthropic_v1_messages.stream_events_from_openai(slow_chunks(), "claude-sonnet-4-20250514", 10))

        self.assertIn("ping", [event.get("type") for event in events])

    def test_catpaw_stream_buffers_partial_tool_marker_before_openai_tool_chunk(self) -> None:
        pieces = ["<tool", "_calls><tool_name>Write</tool_name><parameters>", '{"file_path":"C:/tmp/a.txt","content":"ok"}', "</parameters></tool_calls>"]

        with mock.patch.object(anthropic_v1_messages.openai_v1_chat_complete.catpaw_chat, "chat_completion_deltas", return_value=iter(pieces)):
            chunks = list(
                anthropic_v1_messages.openai_v1_chat_complete.stream_catpaw_tool_chat_completion(
                    {"tools": _claude_code_tools()},
                    [{"role": "user", "content": "write file"}],
                    "claude-sonnet-4-20250514",
                )
            )

        deltas = [chunk["choices"][0]["delta"] for chunk in chunks]
        self.assertFalse(any("<tool" in str(delta.get("content") or "") for delta in deltas))
        tool_delta = next(delta for delta in deltas if delta.get("tool_calls"))
        self.assertEqual(tool_delta["tool_calls"][0]["function"]["name"], "Write")
        self.assertEqual(json.loads(tool_delta["tool_calls"][0]["function"]["arguments"]), {"file_path": "C:/tmp/a.txt", "content": "ok"})
        self.assertEqual(chunks[-1]["choices"][0]["finish_reason"], "tool_calls")

    def test_catpaw_stream_keeps_unparsed_tool_markup_visible(self) -> None:
        pieces = ["<tool_call>Bash</tool_call>"]

        with mock.patch.object(anthropic_v1_messages.openai_v1_chat_complete.catpaw_chat, "chat_completion_deltas", return_value=iter(pieces)):
            chunks = list(
                anthropic_v1_messages.openai_v1_chat_complete.stream_catpaw_tool_chat_completion(
                    {"tools": _claude_code_tools()},
                    [{"role": "user", "content": "write file"}],
                    "claude-sonnet-4-20250514",
                )
            )

        text = "".join(str(chunk["choices"][0]["delta"].get("content") or "") for chunk in chunks)
        self.assertIn("<tool_call>Bash</tool_call>", text)

    def test_catpaw_stream_retries_unfinished_tool_intent_once(self) -> None:
        outputs = [
            iter(["我来帮你创建一个计算器程序。首先创建文件夹，然后编写代码。\n\n</tool_calls>"]),
            iter(['<tool_call>Write<file_path>/home/claude/api/calculator.py</file_path><content>print(1)</content></Write>']),
        ]

        with mock.patch.object(anthropic_v1_messages.openai_v1_chat_complete.catpaw_chat, "chat_completion_deltas", side_effect=outputs):
            chunks = list(
                anthropic_v1_messages.openai_v1_chat_complete.stream_catpaw_tool_chat_completion(
                    {"tools": _claude_code_tools()},
                    [{"role": "user", "content": "create calculator"}],
                    "claude-sonnet-4-20250514",
                )
            )

        tool_delta = next(chunk["choices"][0]["delta"] for chunk in chunks if chunk["choices"][0]["delta"].get("tool_calls"))
        self.assertEqual(tool_delta["tool_calls"][0]["function"]["name"], "Write")
        self.assertEqual(json.loads(tool_delta["tool_calls"][0]["function"]["arguments"])["file_path"], "/home/claude/api/calculator.py")

    def test_catpaw_stream_body_disables_official_agent_auto_mode(self) -> None:
        body = catpaw_client._build_stream_body([{"role": "user", "content": "hello"}], 59)

        self.assertRegex(
            body["conversationId"],
            re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"),
        )
        self.assertEqual(body["userModelTypeCode"], 59)
        self.assertEqual(body["chatApplyModeType"], "chat")
        self.assertIs(body["planPromptEnabled"], False)
        self.assertEqual(body["agentModeConfig"], {"model": {"default": 59, "maxMode": True, "autoMode": False}})


if __name__ == "__main__":
    unittest.main()
