import base64
import json
import random
import time
from typing import Any, Dict, Iterable, Optional

from utils.log import logger


class OrderedMap:
    def __init__(self) -> None:
        self.keys = []
        self.values = {}

    def add(self, key: str, value: Any) -> None:
        if key not in self.values:
            self.keys.append(key)
        self.values[key] = value


def _turnstile_to_str(value: Any) -> str:
    if value is None:
        return "undefined"
    if isinstance(value, float):
        return str(value)
    if isinstance(value, str):
        special = {
            "window.Math": "[object Math]",
            "window.Reflect": "[object Reflect]",
            "window.performance": "[object Performance]",
            "window.localStorage": "[object Storage]",
            "window.Object": "function Object() { [native code] }",
            "window.Reflect.set": "function set() { [native code] }",
            "window.performance.now": "function () { [native code] }",
            "window.Object.create": "function create() { [native code] }",
            "window.Object.keys": "function keys() { [native code] }",
            "window.Math.random": "function random() { [native code] }",
        }
        return special.get(value, value)
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return ",".join(value)
    return str(value)


def _xor_string(text: str, key: str) -> str:
    if not key:
        return text
    return "".join(chr(ord(ch) ^ ord(key[i % len(key)])) for i, ch in enumerate(text))


def _log_turnstile_failure(reason: str, **details: Any) -> None:
    logger.warning({"event": "turnstile_solve_failed", "reason": reason, **details})


def _safe_key(value: Any) -> Any:
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return f"<{type(value).__name__}>"


def _instruction_diagnostics(token: list[Any], process_map: Dict[Any, Any]) -> Dict[str, Any]:
    args = token[1:]
    missing_arg_positions = [index for index, arg in enumerate(args) if arg not in process_map]
    return {
        "arg_count": len(args),
        "arg_key_types": [type(arg).__name__ for arg in args],
        "missing_arg_positions": missing_arg_positions[:8],
        "missing_arg_count": len(missing_arg_positions),
        "numeric_args": [_safe_key(arg) for arg in args if isinstance(arg, (int, float, bool))][:8],
        "process_key_count": len(process_map),
    }


def _is_base64ish(value: str) -> bool:
    if not value or len(value) % 4 != 0:
        return False
    allowed = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/="
    return all(char in allowed for char in value)


def _safe_sorted_keys(keys: set[Any]) -> list[Any]:
    def sort_key(item: Any) -> tuple[str, float, str]:
        if isinstance(item, (int, float, bool)):
            return ("number", float(item), repr(item))
        return (type(item).__name__, 0.0, repr(item))

    return [_safe_key(key) for key in sorted(keys, key=sort_key)]


def _final_state_diagnostics(
    process_map: Dict[Any, Any],
    written_keys: Iterable[Any],
    callable_invocations: list[Dict[str, Any]],
) -> Dict[str, Any]:
    candidate_keys = _candidate_output_keys(process_map, written_keys)
    long_candidate_keys = _long_string_keys(process_map, candidate_keys)
    base64ish_candidate_keys = _base64ish_string_keys(process_map, candidate_keys)
    callable_keys = {key for key, value in process_map.items() if callable(value)}

    return {
        "candidate_output_register_count": len(candidate_keys),
        "candidate_output_register_keys": _safe_sorted_keys(candidate_keys)[:16],
        "long_string_register_keys": _safe_sorted_keys(long_candidate_keys)[:16],
        "base64ish_string_register_keys": _safe_sorted_keys(base64ish_candidate_keys)[:16],
        "callable_register_keys": _safe_sorted_keys(callable_keys)[:16],
        "callable_invocation_count": len(callable_invocations),
        "callable_invocation_tail": callable_invocations[-8:],
    }


def _candidate_output_keys(process_map: Dict[Any, Any], written_keys: Iterable[Any]) -> set[Any]:
    return {
        key
        for key in written_keys
        if isinstance(process_map.get(key), str) and bool(process_map.get(key))
    }


def _long_string_keys(process_map: Dict[Any, Any], keys: set[Any]) -> set[Any]:
    return {
        key
        for key in keys
        if len(process_map[key]) >= 20
    }


def _base64ish_string_keys(process_map: Dict[Any, Any], keys: set[Any]) -> set[Any]:
    return {
        key
        for key in keys
        if _is_base64ish(process_map[key])
    }


def _result_setter_instruction_count(token_list: list[Any], process_map: Dict[Any, Any]) -> int:
    result_setter_instruction_count = 0
    for token in token_list:
        if not isinstance(token, list) or not token:
            continue
        opcode = token[0]
        handler = process_map.get(opcode)
        if opcode == 3 or getattr(handler, "__name__", None) == "func_3":
            result_setter_instruction_count += 1
    return result_setter_instruction_count


def _fallback_result_candidate(
    token_list: list[Any],
    process_map: Dict[Any, Any],
    result_setter_invoked: bool,
    written_keys: list[Any],
) -> Optional[str]:
    if result_setter_invoked or _result_setter_instruction_count(token_list, process_map) != 0:
        return None

    candidate_keys = _candidate_output_keys(process_map, written_keys)
    fallback_keys = _long_string_keys(process_map, candidate_keys) & _base64ish_string_keys(process_map, candidate_keys)
    p_value = process_map.get(16)
    fallback_keys = {key for key in fallback_keys if process_map[key] != p_value}
    if not fallback_keys:
        return None

    for fallback_key in reversed(written_keys):
        if fallback_key in fallback_keys:
            return process_map[fallback_key]
    return None


def _empty_result_diagnostics(
    token_list: list[Any],
    process_map: Dict[Any, Any],
    result_setter_invoked: bool,
    written_keys: Iterable[Any],
    callable_invocations: list[Dict[str, Any]],
) -> Dict[str, Any]:
    result_setter_instruction_count = _result_setter_instruction_count(token_list, process_map)

    tail_opcodes = []
    for token in token_list[-8:]:
        if isinstance(token, list) and token:
            tail_opcodes.append(_safe_key(token[0]))
        else:
            tail_opcodes.append(_safe_key(None))

    return {
        "result_setter_instruction_count": result_setter_instruction_count,
        "result_setter_invoked": result_setter_invoked,
        "tail_opcodes": tail_opcodes,
        "callable_key_count": sum(1 for value in process_map.values() if callable(value)),
        "process_key_count": len(process_map),
        **_final_state_diagnostics(process_map, written_keys, callable_invocations),
    }


def solve_turnstile_token(dx: str, p: str) -> Optional[str]:
    try:
        decoded = base64.b64decode(dx).decode()
        token_list = json.loads(_xor_string(decoded, p))
    except Exception as exc:
        _log_turnstile_failure(
            "decode_failed",
            dx_length=len(dx),
            p_present=bool(p),
            error_type=type(exc).__name__,
        )
        return None

    process_map: Dict[Any, Any] = {}
    start_time = time.time()
    result = ""
    result_setter_invoked = False
    written_keys: list[Any] = []
    callable_invocations: list[Dict[str, Any]] = []

    def record_write(key: Any) -> None:
        if key in written_keys:
            written_keys.remove(key)
        written_keys.append(key)

    def record_callable_invocation(handler: str, target_key: Any, target: Any, args: tuple[Any, ...], raw_args: bool) -> None:
        callable_invocations.append({
            "handler": handler,
            "target_key": _safe_key(target_key),
            "target_handler": getattr(target, "__name__", type(target).__name__),
            "arg_count": len(args),
            "raw_args": raw_args,
        })

    def func_1(e: float, t: float) -> None:
        process_map[e] = _xor_string(_turnstile_to_str(process_map[e]), _turnstile_to_str(process_map[t]))
        record_write(e)

    def func_2(e: float, t: Any) -> None:
        process_map[e] = t
        record_write(e)

    def func_3(e: str) -> None:
        nonlocal result, result_setter_invoked
        result_setter_invoked = True
        result = base64.b64encode(e.encode()).decode()

    def func_5(e: float, t: float) -> None:
        current = process_map[e]
        incoming = process_map[t]
        if isinstance(current, (list, tuple)):
            process_map[e] = list(current) + [incoming]
            record_write(e)
            return
        if isinstance(current, (str, float)) or isinstance(incoming, (str, float)):
            process_map[e] = _turnstile_to_str(current) + _turnstile_to_str(incoming)
            record_write(e)
            return
        process_map[e] = "NaN"
        record_write(e)

    def func_6(e: float, t: float, n: float) -> None:
        tv = process_map[t]
        nv = process_map[n]
        if isinstance(tv, str) and isinstance(nv, str):
            value = f"{tv}.{nv}"
            process_map[e] = "https://chatgpt.com/" if value == "window.document.location" else value
            record_write(e)

    def func_7(e: float, *args: float) -> None:
        target = process_map[e]
        values = [process_map[arg] for arg in args]
        if isinstance(target, str) and target == "window.Reflect.set":
            obj, key_name, val = values
            obj.add(str(key_name), val)
        elif callable(target):
            record_callable_invocation("func_7", e, target, args, False)
            target(*values)

    def func_8(e: float, t: float) -> None:
        process_map[e] = process_map[t] if t in process_map else t
        record_write(e)

    def func_14(e: float, t: float) -> None:
        process_map[e] = json.loads(process_map[t])
        record_write(e)

    def func_15(e: float, t: float) -> None:
        process_map[e] = json.dumps(process_map[t])
        record_write(e)

    def func_17(e: float, t: float, *args: float) -> None:
        call_args = [process_map[arg] for arg in args]
        target = process_map[t]
        if target == "window.performance.now":
            elapsed_ns = time.time_ns() - int(start_time * 1e9)
            process_map[e] = (elapsed_ns + random.random()) / 1e6
            record_write(e)
        elif target == "window.Object.create":
            process_map[e] = OrderedMap()
            record_write(e)
        elif target == "window.Object.keys":
            if call_args and call_args[0] == "window.localStorage":
                process_map[e] = [
                    "STATSIG_LOCAL_STORAGE_INTERNAL_STORE_V4",
                    "STATSIG_LOCAL_STORAGE_STABLE_ID",
                    "client-correlated-secret",
                    "oai/apps/capExpiresAt",
                    "oai-did",
                    "STATSIG_LOCAL_STORAGE_LOGGING_REQUEST",
                    "UiState.isNavigationCollapsed.1",
                ]
                record_write(e)
        elif target == "window.Math.random":
            process_map[e] = random.random()
            record_write(e)
        elif callable(target):
            record_callable_invocation("func_17", t, target, args, False)
            process_map[e] = target(*call_args)
            record_write(e)

    def func_18(e: float) -> None:
        process_map[e] = base64.b64decode(_turnstile_to_str(process_map[e])).decode()
        record_write(e)

    def func_19(e: float) -> None:
        process_map[e] = base64.b64encode(_turnstile_to_str(process_map[e]).encode()).decode()
        record_write(e)

    def func_20(e: float, t: float, n: float, *args: float) -> None:
        if process_map[e] == process_map[t]:
            target = process_map[n]
            if callable(target):
                call_args = [process_map[arg] for arg in args]
                record_callable_invocation("func_20", n, target, args, False)
                target(*call_args)

    def func_21(*_: Any) -> None:
        return

    def func_23(e: float, t: float, *args: float) -> None:
        if process_map[e] is not None and callable(process_map[t]):
            record_callable_invocation("func_23", t, process_map[t], args, True)
            process_map[t](*args)

    def func_24(e: float, t: float, n: float) -> None:
        tv = process_map[t]
        nv = process_map[n]
        if isinstance(tv, str) and isinstance(nv, str):
            process_map[e] = f"{tv}.{nv}"
            record_write(e)

    process_map.update({
        1: func_1,
        2: func_2,
        3: func_3,
        5: func_5,
        6: func_6,
        7: func_7,
        8: func_8,
        9: token_list,
        10: "window",
        14: func_14,
        15: func_15,
        16: p,
        17: func_17,
        18: func_18,
        19: func_19,
        20: func_20,
        21: func_21,
        23: func_23,
        24: func_24,
    })

    for instruction_index, token in enumerate(token_list):
        opcode = token[0]
        fn = process_map.get(opcode)
        if not callable(fn):
            _log_turnstile_failure(
                "unsupported_opcode",
                instruction_index=instruction_index,
                opcode=_safe_key(opcode),
                token_count=len(token_list),
                p_present=bool(p),
                **_instruction_diagnostics(token, process_map),
            )
            continue
        try:
            fn(*token[1:])
        except Exception as exc:
            details = _instruction_diagnostics(token, process_map)
            if isinstance(exc, KeyError):
                details["missing_key"] = _safe_key(exc.args[0] if exc.args else None)
                details["missing_key_type"] = type(exc.args[0]).__name__ if exc.args else "unknown"
            _log_turnstile_failure(
                "instruction_failed",
                instruction_index=instruction_index,
                opcode=_safe_key(opcode),
                handler=getattr(fn, "__name__", type(fn).__name__),
                token_count=len(token_list),
                p_present=bool(p),
                error_type=type(exc).__name__,
                **details,
            )
            continue
    if not result:
        fallback_result = _fallback_result_candidate(token_list, process_map, result_setter_invoked, written_keys)
        if fallback_result is not None:
            return fallback_result

        _log_turnstile_failure(
            "empty_result",
            token_count=len(token_list),
            p_present=bool(p),
            **_empty_result_diagnostics(
                token_list,
                process_map,
                result_setter_invoked,
                written_keys,
                callable_invocations,
            ),
        )
    return result or None
