from __future__ import annotations

import json
import unittest
from typing import Any, cast
from unittest import mock

from test.optional_stubs import install_curl_cffi_stub, install_fastapi_stubs, install_pil_stub, install_pybase64_stub, install_tiktoken_stub

install_curl_cffi_stub()
install_fastapi_stubs()
install_pil_stub()
install_pybase64_stub()
install_tiktoken_stub()

from services.protocol import openai_v1_chat_complete, openai_v1_response
import services.protocol.tool_calls as tool_calls
from services.providers import grok


def _weather_tool() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Get weather for a city.",
            "parameters": {
                "type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"],
            },
        },
    }


class OpenAIToolCallTests(unittest.TestCase):
    def test_parse_xml_tool_calls(self) -> None:
        result = tool_calls.parse_tool_calls(
            '<tool_calls><tool_call><tool_name>get_weather</tool_name><parameters>{"city":"Paris"}</parameters></tool_call></tool_calls>',
            ["get_weather"],
        )

        self.assertTrue(result.saw_tool_syntax)
        self.assertEqual(len(result.calls), 1)
        self.assertEqual(result.calls[0].name, "get_weather")
        self.assertEqual(json.loads(result.calls[0].arguments), {"city": "Paris"})


    def test_invalid_xml_parameters_normalize_to_empty_object(self) -> None:
        malformed = tool_calls.parse_tool_calls(
            '<tool_calls><tool_call><tool_name>get_weather</tool_name><parameters>not-json</parameters></tool_call></tool_calls>',
            ["get_weather"],
        )
        non_object = tool_calls.parse_tool_calls(
            '<tool_calls><tool_call><tool_name>get_weather</tool_name><parameters>[1,2]</parameters></tool_call></tool_calls>',
            ["get_weather"],
        )
        alt_xml = tool_calls.parse_tool_calls(
            '<function_call><name>get_weather</name><arguments>not-json</arguments></function_call>',
            ["get_weather"],
        )

        self.assertEqual(len(malformed.calls), 1)
        self.assertEqual(json.loads(malformed.calls[0].arguments), {})
        self.assertEqual(len(non_object.calls), 1)
        self.assertEqual(json.loads(non_object.calls[0].arguments), {})
        self.assertEqual(len(alt_xml.calls), 1)
        self.assertEqual(json.loads(alt_xml.calls[0].arguments), {})

    def test_parse_json_envelope_and_array_tool_calls(self) -> None:
        envelope = tool_calls.parse_tool_calls('{"tool_calls":[{"name":"get_weather","arguments":{"city":"Paris"}}]}')
        array = tool_calls.parse_tool_calls('[{"name":"get_weather","input":{"city":"Rome"}}]')

        self.assertEqual(json.loads(envelope.calls[0].arguments), {"city": "Paris"})
        self.assertEqual(json.loads(array.calls[0].arguments), {"city": "Rome"})

    def test_chat_non_streaming_converts_model_tool_xml_to_tool_calls(self) -> None:
        body = {
            "model": "unit-test-model",
            "messages": [{"role": "user", "content": "What is the weather?"}],
            "tools": [_weather_tool()],
            "tool_choice": "required",
        }
        xml = '<tool_calls><tool_call><tool_name>get_weather</tool_name><parameters>{"city":"Paris"}</parameters></tool_call></tool_calls>'
        captured: dict[str, Any] = {}

        def fake_collect(_: object, request: Any) -> str:
            captured["messages"] = request.messages
            return xml

        with (
            mock.patch.object(openai_v1_chat_complete, "text_backend", return_value=object()),
            mock.patch.object(openai_v1_chat_complete, "collect_text", side_effect=fake_collect),
        ):
            response = cast(dict[str, Any], openai_v1_chat_complete.handle(body))

        message = response["choices"][0]["message"]
        self.assertIsNone(message["content"])
        self.assertEqual(response["choices"][0]["finish_reason"], "tool_calls")
        self.assertEqual(message["tool_calls"][0]["type"], "function")
        self.assertEqual(message["tool_calls"][0]["function"]["name"], "get_weather")
        self.assertEqual(json.loads(message["tool_calls"][0]["function"]["arguments"]), {"city": "Paris"})
        self.assertIn("TOOL CALL FORMAT", captured["messages"][0]["content"])
        self.assertEqual(captured["messages"][1], {"role": "user", "content": "What is the weather?"})

    def test_responses_non_streaming_converts_model_tool_json_to_function_call_item(self) -> None:
        body = {
            "model": "unit-test-model",
            "input": "What is the weather?",
            "tools": [_weather_tool()],
        }
        text = '{"tool_calls":[{"name":"get_weather","arguments":{"city":"Paris"}}]}'
        captured: dict[str, Any] = {}

        def fake_stream(_: object, request: Any):
            captured["messages"] = request.messages
            yield text

        with (
            mock.patch.object(openai_v1_response, "text_backend", return_value=object()),
            mock.patch.object(openai_v1_response, "stream_text_deltas", side_effect=fake_stream),
        ):
            response = cast(dict[str, Any], openai_v1_response.handle(body))

        self.assertEqual(response["output"][0]["type"], "function_call")
        self.assertEqual(response["output"][0]["name"], "get_weather")
        self.assertEqual(json.loads(response["output"][0]["arguments"]), {"city": "Paris"})
        self.assertIn("TOOL CALL FORMAT", captured["messages"][0]["content"])
        self.assertEqual(captured["messages"][1], {"role": "user", "content": "What is the weather?"})

    def test_grok_search_tools_are_not_injected_or_converted(self) -> None:
        body = {
            "model": "grok-4.3",
            "input": "Search the web.",
            "tools": [{"type": "web_search"}],
        }
        with mock.patch.object(
            grok,
            "console_chat_completion",
            return_value=grok.GrokConsoleCompletion(content="plain search answer"),
        ) as patched_console:
            response = cast(dict[str, Any], openai_v1_response.handle(body))

        patched_console.assert_called_once()
        self.assertEqual(patched_console.call_args.args[0]["tools"], [{"type": "web_search"}])
        self.assertEqual(response["output"][0]["type"], "message")
        self.assertEqual(response["output"][0]["content"][0]["text"], "plain search answer")

    def test_malformed_string_arguments_normalize_to_empty_object(self) -> None:
        malformed = tool_calls.parse_tool_calls('[{"name":"get_weather","arguments":"not-json"}]')
        scalar = tool_calls.parse_tool_calls('[{"name":"get_weather","arguments":"[1,2]"}]')

        self.assertEqual(json.loads(malformed.calls[0].arguments), {})
        self.assertEqual(json.loads(scalar.calls[0].arguments), {})

    def test_chat_streaming_with_tools_buffers_and_emits_tool_call_chunks(self) -> None:
        body = {
            "model": "unit-test-model",
            "stream": True,
            "messages": [{"role": "user", "content": "What is the weather?"}],
            "tools": [_weather_tool()],
        }
        xml_parts = [
            '<tool_calls><tool_call><tool_name>get_weather</tool_name>',
            '<parameters>{"city":"Paris"}</parameters></tool_call></tool_calls>',
        ]

        with (
            mock.patch.object(openai_v1_chat_complete, "text_backend", return_value=object()),
            mock.patch.object(openai_v1_chat_complete, "stream_text_deltas", return_value=iter(xml_parts)),
        ):
            chunks = list(cast(Any, openai_v1_chat_complete.handle(body)))

        deltas = [chunk["choices"][0]["delta"] for chunk in chunks]
        self.assertFalse(any("<tool_calls>" in str(delta.get("content") or "") for delta in deltas))
        tool_delta = next(delta for delta in deltas if delta.get("tool_calls"))
        self.assertEqual(tool_delta["tool_calls"][0]["function"]["name"], "get_weather")
        self.assertEqual(json.loads(tool_delta["tool_calls"][0]["function"]["arguments"]), {"city": "Paris"})
        self.assertEqual(chunks[-1]["choices"][0]["finish_reason"], "tool_calls")

    def test_responses_streaming_with_tools_emits_coherent_function_call_events(self) -> None:
        body = {
            "model": "unit-test-model",
            "stream": True,
            "input": "What is the weather?",
            "tools": [_weather_tool()],
        }
        text_parts = ['{"tool_calls":[{"name":"get_weather","arguments":', '{"city":"Paris"}}]}']

        with (
            mock.patch.object(openai_v1_response, "text_backend", return_value=object()),
            mock.patch.object(openai_v1_response, "stream_text_deltas", return_value=iter(text_parts)),
        ):
            events = list(cast(Any, openai_v1_response.handle(body)))

        event_types = [event.get("type") for event in events]
        self.assertNotIn("response.output_text.delta", event_types)
        self.assertNotIn("response.output_text.done", event_types)
        self.assertIn("response.function_call_arguments.delta", event_types)
        done_item = next(event["item"] for event in events if event.get("type") == "response.output_item.done")
        self.assertEqual(done_item["type"], "function_call")
        self.assertEqual(done_item["name"], "get_weather")
        self.assertEqual(json.loads(done_item["arguments"]), {"city": "Paris"})


if __name__ == "__main__":
    unittest.main()
