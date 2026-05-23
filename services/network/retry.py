from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import random
import time

from curl_cffi import requests
from utils.helper import UpstreamHTTPError


@dataclass(frozen=True)
class RetryPolicy:
    """Controls which upstream failures are retried and how long to wait."""

    max_attempts: int = 3
    retry_statuses: frozenset[int] = frozenset({408, 429, 500, 502, 503, 504})
    base_delay: float = 1.0
    max_delay: float = 30.0
    jitter: bool = True

    def should_retry(self, status_code: int | None, attempt: int) -> bool:
        """Return True when another request attempt should be made."""
        if attempt >= self.max_attempts:
            return False
        if status_code is None:
            return True
        return int(status_code) in self.retry_statuses

    def delay(self, attempt: int, retry_after: int | None = None) -> float:
        """Calculate the sleep duration before the next retry."""
        if retry_after is not None:
            return min(float(retry_after), self.max_delay)

        sleep = min(self.max_delay, self.base_delay * (2 ** attempt))
        if self.jitter:
            sleep *= random.uniform(0.5, 1.5)
        return min(sleep, self.max_delay)

    @staticmethod
    def no_retry() -> RetryPolicy:
        """Return a policy that makes exactly one attempt."""
        return RetryPolicy(max_attempts=1, retry_statuses=frozenset())


DEFAULT_RETRY_POLICY = RetryPolicy()


def retry_call(
    fn: Callable[[], requests.Response],
    policy: RetryPolicy = DEFAULT_RETRY_POLICY,
    deadline: float | None = None,
    on_retry: Callable[[int, int | None, Exception | None], None] | None = None,
) -> requests.Response:
    """Run an HTTP call with retry handling for transient failures."""
    attempt = 1

    while True:
        try:
            response = fn()
        except UpstreamHTTPError as exc:
            status_code: int | None = exc.status_code
            retry_after = exc.retry_after
            last_exc: Exception | None = exc
        except requests.exceptions.RequestException as exc:
            status_code = None
            retry_after = None
            last_exc = exc
        else:
            status_code = int(response.status_code)
            if not policy.should_retry(status_code, attempt):
                return response
            last_exc = None
            retry_after = _retry_after(response)

        if deadline is not None and time.monotonic() >= deadline:
            if last_exc is not None:
                raise last_exc
            return response
        if last_exc is not None and not policy.should_retry(status_code, attempt):
            raise last_exc

        if on_retry is not None:
            on_retry(attempt, status_code, last_exc)

        sleep_for = policy.delay(attempt, retry_after)
        if deadline is not None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                if last_exc is not None:
                    raise last_exc
                return response
            sleep_for = min(sleep_for, remaining)
        time.sleep(sleep_for)
        attempt += 1


def _retry_after(response: requests.Response) -> int | None:
    header = response.headers.get("Retry-After") if hasattr(response, "headers") else None
    if header is None:
        return None
    retry_after = str(header).strip()
    if retry_after.isdigit():
        return int(retry_after)
    return None
