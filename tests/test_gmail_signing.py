"""
Tests for _feedback_action_url() in notifier/gmail.py.

Run: python -m pytest tests/test_gmail_signing.py -v
"""
import os
import time
import hmac
import hashlib
import pytest
from unittest.mock import patch


def _sign(secret: str, job_id: str, action: str) -> str:
    """Mirror of the signing logic in _feedback_action_url."""
    day_bucket = int(time.time()) // 86400
    payload = f"{job_id}:{action}:{day_bucket}"
    return hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()[:16]


class TestFeedbackActionUrl:

    def test_fallback_when_no_cf_url(self):
        """Returns localhost /fb URL when CF_WORKER_URL is not set."""
        env = {"CF_WORKER_URL": "", "CF_WORKER_SECRET": ""}
        with patch.dict(os.environ, env, clear=False):
            from notifier.gmail import _feedback_action_url
            url = _feedback_action_url("42", "like")
        assert "localhost:5001" in url
        assert "id=42" in url
        assert "a=like" in url

    def test_fallback_when_no_secret(self):
        """Returns localhost URL when secret is missing even if URL is set."""
        env = {"CF_WORKER_URL": "https://worker.example.com", "CF_WORKER_SECRET": ""}
        with patch.dict(os.environ, env, clear=False):
            from notifier.gmail import _feedback_action_url
            url = _feedback_action_url("99", "pass")
        assert "localhost:5001" in url

    def test_cf_like_url_contains_correct_route(self):
        """CF like action uses /feedback route."""
        env = {"CF_WORKER_URL": "https://worker.example.com", "CF_WORKER_SECRET": "testsecret"}
        with patch.dict(os.environ, env, clear=False):
            from notifier.gmail import _feedback_action_url
            import importlib; import notifier.gmail as gm; importlib.reload(gm)
            url = gm._feedback_action_url("7", "like")
        assert "/feedback" in url
        assert "job_id=7" in url
        assert "action=like" in url
        assert "sig=" in url

    def test_cf_pass_url_contains_correct_route(self):
        """CF pass action uses /feedback route."""
        env = {"CF_WORKER_URL": "https://worker.example.com", "CF_WORKER_SECRET": "testsecret"}
        with patch.dict(os.environ, env, clear=False):
            import importlib; import notifier.gmail as gm; importlib.reload(gm)
            url = gm._feedback_action_url("7", "pass")
        assert "/feedback" in url
        assert "action=pass" in url

    def test_cf_rate_url_uses_rate_route(self):
        """CF rate action uses /rate route (not /feedback)."""
        env = {"CF_WORKER_URL": "https://worker.example.com", "CF_WORKER_SECRET": "testsecret"}
        with patch.dict(os.environ, env, clear=False):
            import importlib; import notifier.gmail as gm; importlib.reload(gm)
            url = gm._feedback_action_url("7", "rate")
        assert "/rate" in url
        assert "/feedback" not in url
        assert "job_id=7" in url

    def test_hmac_signature_is_correct(self):
        """The sig= param matches the expected HMAC for today's daily bucket."""
        secret = "supersecret123"
        env = {"CF_WORKER_URL": "https://worker.example.com", "CF_WORKER_SECRET": secret}
        with patch.dict(os.environ, env, clear=False):
            import importlib; import notifier.gmail as gm; importlib.reload(gm)
            url = gm._feedback_action_url("55", "like")

        # Extract sig from URL
        from urllib.parse import urlparse, parse_qs
        parsed = urlparse(url)
        sig_in_url = parse_qs(parsed.query)["sig"][0]

        expected_sig = _sign(secret, "55", "like")
        assert sig_in_url == expected_sig

    def test_different_jobs_produce_different_sigs(self):
        """Two different job IDs produce different signatures."""
        secret = "abc"
        env = {"CF_WORKER_URL": "https://worker.example.com", "CF_WORKER_SECRET": secret}
        with patch.dict(os.environ, env, clear=False):
            import importlib; import notifier.gmail as gm; importlib.reload(gm)
            url1 = gm._feedback_action_url("1", "like")
            url2 = gm._feedback_action_url("2", "like")

        from urllib.parse import urlparse, parse_qs
        sig1 = parse_qs(urlparse(url1).query)["sig"][0]
        sig2 = parse_qs(urlparse(url2).query)["sig"][0]
        assert sig1 != sig2
