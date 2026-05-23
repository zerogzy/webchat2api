from __future__ import annotations

import base64
import json
import unittest
from unittest import mock

from utils.turnstile import _instruction_diagnostics, _xor_string, solve_turnstile_token


def _build_dx(token_list: list[list[object]], p: str = "") -> str:
    payload = json.dumps(token_list)
    encoded = _xor_string(payload, p)
    return base64.b64encode(encoded.encode()).decode()


class TurnstileSolverTests(unittest.TestCase):
    def test_solver_decodes_challenge_with_non_empty_p(self) -> None:
        token = solve_turnstile_token(_build_dx([[3, "decoded-with-p"]], "synthetic-p"), "synthetic-p")

        self.assertEqual(token, base64.b64encode(b"decoded-with-p").decode())

    def test_func_23_passes_raw_register_keys_to_target(self) -> None:
        encoded_value = base64.b64encode(b"decoded-through-register-key").decode()
        token = solve_turnstile_token(
            _build_dx([
                [2, 30, encoded_value],
                [23, 16, 18, 30],
                [7, 3, 30],
            ], "synthetic-p"),
            "synthetic-p",
        )

        self.assertEqual(token, base64.b64encode(b"decoded-through-register-key").decode())

    def test_func_8_copies_literal_when_source_key_is_missing(self) -> None:
        token = solve_turnstile_token(
            _build_dx([
                [8, 30, "literal-value"],
                [7, 3, 30],
            ], "synthetic-p"),
            "synthetic-p",
        )

        self.assertEqual(token, base64.b64encode(b"literal-value").decode())

    def test_instruction_failures_log_diagnostics_without_secret_material(self) -> None:
        dx = _build_dx([[98], [3, "safe-result"]], "secret-p")

        with mock.patch("utils.turnstile.logger.warning") as warning:
            token = solve_turnstile_token(dx, "secret-p")

        self.assertEqual(token, base64.b64encode(b"safe-result").decode())
        warning.assert_called_once()
        payload = warning.call_args.args[0]
        self.assertEqual(payload["event"], "turnstile_solve_failed")
        self.assertEqual(payload["reason"], "unsupported_opcode")
        self.assertEqual(payload["opcode"], 98)
        self.assertEqual(payload["instruction_index"], 0)
        self.assertEqual(payload["token_count"], 2)
        self.assertTrue(payload["p_present"])
        self.assertNotIn("dx", payload)
        self.assertNotIn("p", payload)
        self.assertNotIn("token", payload)
        self.assertNotIn("secret-p", repr(payload))
        self.assertNotIn(dx, repr(payload))

    def test_instruction_failure_logs_missing_key_diagnostics_without_secret_material(self) -> None:
        dx = _build_dx([[8, 42, 18], [42, 99], [3, "safe-result"]], "secret-p")

        with mock.patch("utils.turnstile.logger.warning") as warning:
            token = solve_turnstile_token(dx, "secret-p")

        self.assertEqual(token, base64.b64encode(b"safe-result").decode())
        payload = warning.call_args_list[0].args[0]
        self.assertEqual(payload["event"], "turnstile_solve_failed")
        self.assertEqual(payload["reason"], "instruction_failed")
        self.assertEqual(payload["opcode"], 42)
        self.assertEqual(payload["handler"], "func_18")
        self.assertEqual(payload["error_type"], "KeyError")
        self.assertEqual(payload["missing_key"], 99)
        self.assertEqual(payload["missing_key_type"], "int")
        self.assertEqual(payload["arg_count"], 1)
        self.assertEqual(payload["missing_arg_positions"], [0])
        self.assertEqual(payload["missing_arg_count"], 1)
        self.assertEqual(payload["numeric_args"], [99])
        self.assertNotIn("dx", payload)
        self.assertNotIn("p", payload)
        self.assertNotIn("token", payload)
        self.assertNotIn("secret-p", repr(payload))
        self.assertNotIn(dx, repr(payload))

    def test_instruction_diagnostics_redacts_non_numeric_argument_values(self) -> None:
        payload = _instruction_diagnostics([98, "secret-value", 7], {98: object(), 7: "safe"})

        self.assertEqual(payload["arg_count"], 2)
        self.assertEqual(payload["arg_key_types"], ["str", "int"])
        self.assertEqual(payload["missing_arg_positions"], [0])
        self.assertEqual(payload["missing_arg_count"], 1)
        self.assertEqual(payload["numeric_args"], [7])
        self.assertEqual(payload["process_key_count"], 2)
        self.assertNotIn("secret-value", repr(payload))

    def test_empty_result_logs_sanitized_vm_diagnostics_without_secret_material(self) -> None:
        dx = _build_dx([
            [2, 30, "secret-instruction-value"],
            [8, 40, 3],
            [3, ""],
            [40, ""],
            [21, "secret-tail-value"],
        ], "secret-p")

        with mock.patch("utils.turnstile.logger.warning") as warning:
            token = solve_turnstile_token(dx, "secret-p")

        self.assertIsNone(token)
        warning.assert_called_once()
        payload = warning.call_args.args[0]
        self.assertEqual(payload["event"], "turnstile_solve_failed")
        self.assertEqual(payload["reason"], "empty_result")
        self.assertEqual(payload["token_count"], 5)
        self.assertTrue(payload["p_present"])
        self.assertEqual(payload["result_setter_instruction_count"], 2)
        self.assertTrue(payload["result_setter_invoked"])
        self.assertEqual(payload["tail_opcodes"], [2, 8, 3, 40, 21])
        self.assertGreaterEqual(payload["callable_key_count"], 13)
        self.assertGreaterEqual(payload["process_key_count"], 21)
        self.assertEqual(payload["candidate_output_register_count"], 1)
        self.assertEqual(payload["candidate_output_register_keys"], [30])
        self.assertEqual(payload["long_string_register_keys"], [30])
        self.assertEqual(payload["base64ish_string_register_keys"], [])
        self.assertIn(3, payload["callable_register_keys"])
        self.assertEqual(payload["callable_invocation_count"], 0)
        self.assertEqual(payload["callable_invocation_tail"], [])
        self.assertNotIn("dx", payload)
        self.assertNotIn("p", payload)
        self.assertNotIn("token", payload)
        self.assertNotIn("secret-p", repr(payload))
        self.assertNotIn("secret-instruction-value", repr(payload))
        self.assertNotIn("secret-tail-value", repr(payload))
        self.assertNotIn(dx, repr(payload))

    def test_empty_result_tail_opcodes_redact_non_numeric_instruction_keys(self) -> None:
        dx = _build_dx([
            ["secret-opcode", "secret-arg"],
            [21, "secret-tail-value"],
        ], "secret-p")

        with mock.patch("utils.turnstile.logger.warning") as warning:
            token = solve_turnstile_token(dx, "secret-p")

        self.assertIsNone(token)
        empty_result_payload = warning.call_args_list[-1].args[0]
        self.assertEqual(empty_result_payload["reason"], "empty_result")
        self.assertEqual(empty_result_payload["tail_opcodes"], ["<str>", 21])
        self.assertEqual(empty_result_payload["result_setter_instruction_count"], 0)
        self.assertFalse(empty_result_payload["result_setter_invoked"])
        self.assertEqual(empty_result_payload["candidate_output_register_count"], 0)
        self.assertEqual(empty_result_payload["candidate_output_register_keys"], [])
        self.assertEqual(empty_result_payload["long_string_register_keys"], [])
        self.assertEqual(empty_result_payload["base64ish_string_register_keys"], [])
        self.assertNotIn("secret-opcode", repr(empty_result_payload))
        self.assertNotIn("secret-arg", repr(empty_result_payload))
        self.assertNotIn("secret-tail-value", repr(empty_result_payload))
        self.assertNotIn("secret-p", repr(empty_result_payload))

    def test_empty_result_logs_sanitized_callable_shape_without_secret_material(self) -> None:
        dx = _build_dx([
            [2, 30, base64.b64encode(b"secret-output-candidate").decode()],
            [23, 16, 18, 30],
            [8, 75.98, 30],
        ], "secret-p")

        with mock.patch("utils.turnstile.logger.warning") as warning:
            token = solve_turnstile_token(dx, "secret-p")

        self.assertIsNone(token)
        payload = warning.call_args.args[0]
        self.assertEqual(payload["event"], "turnstile_solve_failed")
        self.assertEqual(payload["reason"], "empty_result")
        self.assertEqual(payload["tail_opcodes"], [2, 23, 8])
        self.assertEqual(payload["candidate_output_register_count"], 2)
        self.assertEqual(payload["candidate_output_register_keys"], [30, 75.98])
        self.assertEqual(payload["long_string_register_keys"], [30, 75.98])
        self.assertEqual(payload["base64ish_string_register_keys"], [])
        self.assertEqual(payload["callable_invocation_count"], 1)
        self.assertEqual(payload["callable_invocation_tail"], [{
            "handler": "func_23",
            "target_key": 18,
            "target_handler": "func_18",
            "arg_count": 1,
            "raw_args": True,
        }])
        self.assertNotIn("dx", payload)
        self.assertNotIn("p", payload)
        self.assertNotIn("token", payload)
        self.assertNotIn("secret-p", repr(payload))
        self.assertNotIn("secret-output-candidate", repr(payload))
        self.assertNotIn(dx, repr(payload))

    def test_unique_written_long_base64ish_candidate_returns_as_token_without_logging(self) -> None:
        candidate = base64.b64encode(b"secret-generated-token").decode()
        dx = _build_dx([
            [2, 30, candidate],
            [2, 99.99, "short"],
        ], "secret-p")

        with mock.patch("utils.turnstile.logger.warning") as warning:
            token = solve_turnstile_token(dx, "secret-p")

        self.assertEqual(token, candidate)
        warning.assert_not_called()

    def test_multiple_written_long_base64ish_candidates_returns_latest_written(self) -> None:
        first_candidate = base64.b64encode(b"secret-generated-token-one").decode()
        second_candidate = base64.b64encode(b"secret-generated-token-two").decode()
        dx = _build_dx([
            [2, 30, first_candidate],
            [2, 40, second_candidate],
        ], "secret-p")

        with mock.patch("utils.turnstile.logger.warning") as warning:
            token = solve_turnstile_token(dx, "secret-p")

        self.assertEqual(token, second_candidate)
        warning.assert_not_called()

    def test_fallback_selects_rewritten_register_as_latest_candidate(self) -> None:
        first_candidate = base64.b64encode(b"secret-generated-token-one").decode()
        second_candidate = base64.b64encode(b"secret-generated-token-two").decode()
        rewritten_candidate = base64.b64encode(b"secret-generated-token-three").decode()
        dx = _build_dx([
            [2, 30, first_candidate],
            [2, 40, second_candidate],
            [2, 30, rewritten_candidate],
        ], "secret-p")

        with mock.patch("utils.turnstile.logger.warning") as warning:
            token = solve_turnstile_token(dx, "secret-p")

        self.assertEqual(token, rewritten_candidate)
        warning.assert_not_called()

    def test_fallback_selects_latest_written_and_excludes_p(self) -> None:
        p_candidate = base64.b64encode(b"secret-p-derived-candidate").decode()
        first_candidate = base64.b64encode(b"secret-generated-token-one").decode()
        second_candidate = base64.b64encode(b"secret-generated-token-two").decode()
        dx = _build_dx([
            [8, 16.74, 16],
            [2, 28.21, first_candidate],
            [2, 30.33, second_candidate],
        ], p_candidate)

        with mock.patch("utils.turnstile.logger.warning") as warning:
            token = solve_turnstile_token(dx, p_candidate)

        self.assertEqual(token, second_candidate)
        warning.assert_not_called()

    def test_fallback_ignores_unwritten_base64ish_registers(self) -> None:
        candidate = base64.b64encode(b"secret-generated-token").decode()
        dx = _build_dx([
            [2, 30, candidate],
            [23, 16, 2, 99.99, "unused-base64ish-register"],
        ], "secret-p")

        with mock.patch("utils.turnstile.logger.warning") as warning:
            token = solve_turnstile_token(dx, "secret-p")

        self.assertEqual(token, candidate)
        warning.assert_not_called()

    def test_decode_failure_logs_only_lengths_and_error_type(self) -> None:
        dx = "not-json"

        with mock.patch("utils.turnstile.logger.warning") as warning:
            token = solve_turnstile_token(dx, "secret-p")

        self.assertIsNone(token)
        warning.assert_called_once()
        payload = warning.call_args.args[0]
        self.assertEqual(payload["event"], "turnstile_solve_failed")
        self.assertEqual(payload["reason"], "decode_failed")
        self.assertEqual(payload["dx_length"], len(dx))
        self.assertTrue(payload["p_present"])
        self.assertIn("error_type", payload)
        self.assertNotIn("dx", payload)
        self.assertNotIn("p", payload)
        self.assertNotIn("secret-p", repr(payload))
        self.assertNotIn(dx, repr(payload))


if __name__ == "__main__":
    unittest.main()
