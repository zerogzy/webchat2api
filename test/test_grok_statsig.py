from __future__ import annotations

import base64
import unittest
from unittest import mock

from services.providers.grok.statsig import GrokStatsigSigner, _validate_signer_url, valid_statsig_id


class _Response:
    def __init__(self, *, text: str = "", payload: object = None, status_code: int = 200) -> None:
        self.text = text
        self.payload = payload
        self.status_code = status_code

    def json(self) -> object:
        return self.payload


class _Session:
    def __init__(self, response: _Response) -> None:
        self.response = response
        self.calls: list[tuple[object, ...]] = []

    def get(self, *args: object, **kwargs: object) -> _Response:
        self.calls.append(args)
        return self.response

    def post(self, *args: object, **kwargs: object) -> _Response:
        self.calls.append(args)
        return self.response

    def close(self) -> None:
        pass


class GrokStatsigTests(unittest.TestCase):
    def test_valid_statsig_id_matches_upstream_binary_shape(self) -> None:
        value = base64.b64encode(b"x" * 70).decode()

        self.assertTrue(valid_statsig_id(value))
        self.assertFalse(valid_statsig_id("0196a8f6-0501-79f8-8d74-a2f2c0f5f5f5"))

    def test_sign_fetches_verification_meta_and_caches_by_method_and_path(self) -> None:
        signature = base64.b64encode(b"s" * 70).decode()
        upstream = _Session(_Response(text='<meta name="grok-site-verification" content="meta-value">'))
        signing = _Session(_Response(payload={"x-statsig-id": signature}))
        signer = GrokStatsigSigner()

        with mock.patch("services.providers.grok.statsig.create_session", return_value=signing):
            first = signer.sign(upstream, {"Cookie": "sso=secret"}, "https://grok.wodf.de/sign", "POST", "https://grok.com/rest/rate-limits")
            second = signer.sign(upstream, {"Cookie": "sso=secret"}, "https://grok.wodf.de/sign", "POST", "https://grok.com/rest/rate-limits")

        self.assertEqual(first, signature)
        self.assertEqual(second, signature)
        self.assertEqual(len(upstream.calls), 1)
        self.assertEqual(len(signing.calls), 1)

    def test_signer_url_allows_public_https_and_internal_services(self) -> None:
        for value in (
            "https://grok.wodf.de/sign",
            "http://grok-signer:8788/sign",
            "http://127.0.0.1:8788/sign",
        ):
            with self.subTest(value=value):
                self.assertEqual(_validate_signer_url(value), value)

    def test_signer_url_rejects_unsafe_public_endpoints(self) -> None:
        for value in (
            "http://example.com/sign",
            "https://example.com:8443/sign",
            "https://user:pass@example.com/sign",
            "https://example.com/sign?token=value",
            "https://example.com/sign#fragment",
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                _validate_signer_url(value)

    def test_signer_failure_is_cached_briefly(self) -> None:
        upstream = _Session(_Response(status_code=503))
        signer = GrokStatsigSigner(failure_ttl_seconds=30)

        for _ in range(2):
            with self.assertRaisesRegex(RuntimeError, "HTTP 503"):
                signer.sign(
                    upstream,
                    {},
                    "https://grok.wodf.de/sign",
                    "POST",
                    "https://grok.com/rest/rate-limits",
                )

        self.assertEqual(len(upstream.calls), 1)

    def test_expired_signature_is_used_when_refresh_fails(self) -> None:
        signature = base64.b64encode(b"s" * 70).decode()
        upstream = _Session(_Response(text='<meta name="grok-site-verification" content="meta-value">'))
        signing = _Session(_Response(payload={"x-statsig-id": signature}))
        signer = GrokStatsigSigner(ttl_seconds=0)

        with mock.patch("services.providers.grok.statsig.create_session", return_value=signing):
            self.assertEqual(
                signer.sign(upstream, {}, "https://grok.wodf.de/sign", "POST", "https://grok.com/rest/rate-limits"),
                signature,
            )
        upstream.response = _Response(status_code=503)

        self.assertEqual(
            signer.sign(upstream, {}, "https://grok.wodf.de/sign", "POST", "https://grok.com/rest/rate-limits"),
            signature,
        )


if __name__ == "__main__":
    unittest.main()
