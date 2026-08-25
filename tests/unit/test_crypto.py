from __future__ import annotations

import warnings

from internship_agent.crypto import SecretBox


class TestSecretBox:
    def test_roundtrip(self):
        box = SecretBox("some-secret")
        token = box.encrypt("sk-abc123")
        assert token != b"sk-abc123"
        assert box.decrypt(token) == "sk-abc123"

    def test_none_and_empty_string_pass_through_as_none(self):
        box = SecretBox("some-secret")
        assert box.encrypt(None) is None
        assert box.encrypt("") is None
        assert box.decrypt(None) is None

    def test_ciphertext_does_not_contain_plaintext(self):
        box = SecretBox("some-secret")
        secret = "hunter-io-key-should-not-leak"
        token = box.encrypt(secret)
        assert secret.encode() not in token

    def test_wrong_key_fails_to_decrypt(self):
        token = SecretBox("key-a").encrypt("sk-abc123")
        assert SecretBox("key-b").decrypt(token) is None

    def test_falls_back_to_env_secret_with_warning(self, monkeypatch):
        monkeypatch.delenv("INTERNSHIP_AGENT_SECRET_KEY", raising=False)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            box = SecretBox()
            assert any(issubclass(w.category, RuntimeWarning) for w in caught)
        token = box.encrypt("sk-abc123")
        assert SecretBox().decrypt(token) == "sk-abc123"
