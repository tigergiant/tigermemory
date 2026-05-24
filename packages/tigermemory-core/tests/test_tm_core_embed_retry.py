"""Tests for tools/tm_core.py embedding retry + circuit breaker.

Why these tests exist: Phase 5b-0 audit identified that tm_core embed had no
transient/permanent classification, no backoff, no breaker — Phase 5 first-run
50% HTTP 500 was the symptom. We added the OpenViking-inspired stability layer
(`wiki/systems/openviking-upstream-grounding.md` §6, §9.1). These tests cover
the contract: retry on transient, fail-fast on permanent, breaker after N
consecutive transient failures.

Uses plain `unittest` (no pytest dependency) so it runs anywhere with Python
3.9+. pytest will still discover these via the unittest compat layer.
"""
from __future__ import annotations

import pathlib
import sys
import unittest
from unittest import mock

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

import tigermemory_core as tm_core  # type: ignore[import-not-found]  # noqa: E402


_FAKE_CFG = {
    "base": "http://fake.local/v1",
    "model": "fake-embed",
    "api_key": "REDACTED",
    "dim": None,
}


class _CallTracker:
    """Stateful side_effect for `_embed_batch_once`. Pops one outcome per call.

    Each item is either:
    - a list of vectors (returned on success), or
    - an EmbeddingError instance (raised).
    """

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    def __call__(self, batch, cfg, timeout):
        self.calls += 1
        if not self.outcomes:
            raise AssertionError(
                f"_embed_batch_once called {self.calls} time(s); no more outcomes queued"
            )
        item = self.outcomes.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


def _ok_vectors(n=1, dim=4):
    return [[0.1] * dim for _ in range(n)]


def _transient(status=500, message="server overloaded"):
    return tm_core.EmbeddingError(
        f"Embedding HTTP {status}: {message}", kind="transient", status=status
    )


def _permanent(status=400, message="bad request"):
    return tm_core.EmbeddingError(
        f"Embedding HTTP {status}: {message}", kind="permanent", status=status
    )


class EmbedRetryBreakerTests(unittest.TestCase):

    def setUp(self):
        # Reset breaker so tests are order-independent.
        tm_core._EMBED_BREAKER.reset()
        # Patch sleep so tests don't actually wait for backoff.
        self._sleep_patcher = mock.patch.object(tm_core, "_embed_sleep")
        self.mock_sleep = self._sleep_patcher.start()
        # Speed up tunables: shorter delays + fewer threshold for tests that
        # don't override explicitly. We do this via env so the production path
        # in `_embed_retry_config` is exercised.
        self._env_patcher = mock.patch.dict(
            "os.environ",
            {
                "EMBEDDING_MAX_RETRIES": "3",
                "EMBEDDING_BASE_DELAY": "0.01",
                "EMBEDDING_MAX_DELAY": "0.02",
                "EMBEDDING_BREAKER_THRESHOLD": "5",
                "EMBEDDING_BREAKER_RESET": "60",
            },
            clear=False,
        )
        self._env_patcher.start()

    def tearDown(self):
        self._sleep_patcher.stop()
        self._env_patcher.stop()
        tm_core._EMBED_BREAKER.reset()

    # ---- core retry behavior ----

    def test_two_500s_then_success(self):
        """Transient 500 twice, then success: 3 underlying calls, returns vectors."""
        tracker = _CallTracker([
            _transient(500),
            _transient(500),
            _ok_vectors(n=1),
        ])
        with mock.patch.object(tm_core, "_embed_batch_once", side_effect=tracker):
            out = tm_core._embed_batch(["hello"], _FAKE_CFG, 30)
        self.assertEqual(tracker.calls, 3)
        self.assertEqual(len(out), 1)
        self.assertEqual(tm_core._EMBED_BREAKER.state, tm_core._EMBED_BREAKER.CLOSED)
        # Sleep should have been called between attempts (2 retries -> 2 sleeps).
        self.assertEqual(self.mock_sleep.call_count, 2)

    def test_429_then_success(self):
        tracker = _CallTracker([_transient(429, "rate limit"), _ok_vectors(n=2)])
        with mock.patch.object(tm_core, "_embed_batch_once", side_effect=tracker):
            out = tm_core._embed_batch(["a", "b"], _FAKE_CFG, 30)
        self.assertEqual(tracker.calls, 2)
        self.assertEqual(len(out), 2)
        self.assertEqual(self.mock_sleep.call_count, 1)

    def test_400_no_retry(self):
        """Permanent error must fail immediately, exactly 1 underlying call."""
        tracker = _CallTracker([_permanent(400, "invalid request")])
        with mock.patch.object(tm_core, "_embed_batch_once", side_effect=tracker):
            with self.assertRaises(tm_core.EmbeddingError) as cm:
                tm_core._embed_batch(["x"], _FAKE_CFG, 30)
        self.assertEqual(cm.exception.kind, "permanent")
        self.assertEqual(cm.exception.status, 400)
        self.assertEqual(tracker.calls, 1)
        self.assertEqual(self.mock_sleep.call_count, 0)

    def test_404_model_not_found_no_retry(self):
        tracker = _CallTracker([_permanent(404, "InvalidEndpointOrModel.NotFound")])
        with mock.patch.object(tm_core, "_embed_batch_once", side_effect=tracker):
            with self.assertRaises(tm_core.EmbeddingError):
                tm_core._embed_batch(["x"], _FAKE_CFG, 30)
        self.assertEqual(tracker.calls, 1)

    def test_shape_mismatch_no_retry(self):
        """Shape mismatch is classified permanent; must not loop."""
        tracker = _CallTracker([
            tm_core.EmbeddingError(
                "Embedding shape mismatch: expected 2 vectors, got 1",
                kind="permanent",
            )
        ])
        with mock.patch.object(tm_core, "_embed_batch_once", side_effect=tracker):
            with self.assertRaises(tm_core.EmbeddingError):
                tm_core._embed_batch(["x", "y"], _FAKE_CFG, 30)
        self.assertEqual(tracker.calls, 1)

    # ---- circuit breaker ----

    def test_breaker_opens_after_threshold(self):
        """5 consecutive transient failures (across calls) -> breaker OPEN."""
        # max_retries=3 => first call exhausts retries -> 1 transient failure recorded.
        # We need 5 consecutive *failed* calls (each with all retries exhausted).
        outcomes = [_transient(500)] * (4 * 5)  # 4 attempts per call * 5 calls
        tracker = _CallTracker(outcomes)
        with mock.patch.object(tm_core, "_embed_batch_once", side_effect=tracker):
            for _ in range(5):
                with self.assertRaises(tm_core.EmbeddingError):
                    tm_core._embed_batch(["x"], _FAKE_CFG, 30)
        self.assertEqual(tm_core._EMBED_BREAKER.state, tm_core._EMBED_BREAKER.OPEN)
        self.assertGreaterEqual(tm_core._EMBED_BREAKER.failure_count, 5)

    def test_breaker_open_fast_fails_subsequent_calls(self):
        """Once OPEN, next call must fast-fail without invoking _embed_batch_once."""
        # Force breaker open directly to keep the test fast and focused.
        tm_core._EMBED_BREAKER.state = tm_core._EMBED_BREAKER.OPEN
        tm_core._EMBED_BREAKER.failure_count = 5
        import time as _time
        tm_core._EMBED_BREAKER.opened_at = _time.monotonic()  # just opened, not elapsed

        tracker = _CallTracker([])  # no outcomes — must NOT be called
        with mock.patch.object(tm_core, "_embed_batch_once", side_effect=tracker):
            with self.assertRaises(tm_core.EmbeddingError) as cm:
                tm_core._embed_batch(["x"], _FAKE_CFG, 30)
        self.assertEqual(tracker.calls, 0)
        self.assertIn("circuit breaker OPEN", str(cm.exception))

    def test_breaker_half_open_probe_success_closes(self):
        """After reset window elapses, one probe success closes the breaker."""
        tm_core._EMBED_BREAKER.state = tm_core._EMBED_BREAKER.OPEN
        tm_core._EMBED_BREAKER.failure_count = 5
        import time as _time
        # Open it 999s ago — well beyond the 60s reset window.
        tm_core._EMBED_BREAKER.opened_at = _time.monotonic() - 999.0

        tracker = _CallTracker([_ok_vectors(n=1)])
        with mock.patch.object(tm_core, "_embed_batch_once", side_effect=tracker):
            out = tm_core._embed_batch(["probe"], _FAKE_CFG, 30)
        self.assertEqual(len(out), 1)
        self.assertEqual(tm_core._EMBED_BREAKER.state, tm_core._EMBED_BREAKER.CLOSED)
        self.assertEqual(tm_core._EMBED_BREAKER.failure_count, 0)

    def test_success_resets_failure_count(self):
        """A successful call clears accumulated transient counter (CLOSED state)."""
        # Two transient-then-success calls; counter should not climb to threshold.
        for _ in range(3):
            tracker = _CallTracker([_transient(503), _ok_vectors(n=1)])
            with mock.patch.object(tm_core, "_embed_batch_once", side_effect=tracker):
                tm_core._embed_batch(["x"], _FAKE_CFG, 30)
        self.assertEqual(tm_core._EMBED_BREAKER.state, tm_core._EMBED_BREAKER.CLOSED)
        self.assertEqual(tm_core._EMBED_BREAKER.failure_count, 0)


class ClassifyTests(unittest.TestCase):

    def test_status_permanent(self):
        for s in (400, 401, 403, 404, 422):
            self.assertEqual(
                tm_core._classify_embedding_failure(s, "whatever"), "permanent",
                f"status {s} should be permanent",
            )

    def test_status_transient(self):
        for s in (408, 429, 500, 502, 503, 504):
            self.assertEqual(
                tm_core._classify_embedding_failure(s, ""), "transient",
                f"status {s} should be transient",
            )

    def test_text_pattern_permanent(self):
        self.assertEqual(
            tm_core._classify_embedding_failure(None, "InvalidEndpointOrModel.NotFound"),
            "permanent",
        )

    def test_text_pattern_transient(self):
        self.assertEqual(
            tm_core._classify_embedding_failure(None, "Connection reset by peer"),
            "transient",
        )

    def test_unknown(self):
        self.assertEqual(
            tm_core._classify_embedding_failure(None, "unrelated weirdness"),
            "unknown",
        )


if __name__ == "__main__":
    unittest.main()
