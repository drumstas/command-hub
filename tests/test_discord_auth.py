from __future__ import annotations

from nacl.signing import SigningKey

from app.command_hub import verify_discord_signature


def test_discord_signature_accepts_signed_request(monkeypatch):
    signing_key = SigningKey.generate()
    monkeypatch.setenv("DISCORD_PUBLIC_KEY", signing_key.verify_key.encode().hex())
    timestamp = "1711111111"
    body = b'{"type":1}'
    signature = signing_key.sign(timestamp.encode() + b"." + body).signature.hex()

    assert verify_discord_signature(body, signature, timestamp)


def test_discord_signature_rejects_modified_request(monkeypatch):
    signing_key = SigningKey.generate()
    monkeypatch.setenv("DISCORD_PUBLIC_KEY", signing_key.verify_key.encode().hex())
    timestamp = "1711111111"
    signature = signing_key.sign(timestamp.encode() + b'.{"type":1}').signature.hex()

    assert not verify_discord_signature(b'{"type":2}', signature, timestamp)


def test_discord_signature_rejects_missing_or_malformed_values(monkeypatch):
    monkeypatch.setenv("DISCORD_PUBLIC_KEY", "not-a-public-key")

    assert not verify_discord_signature(b"{}", None, "1711111111")
    assert not verify_discord_signature(b"{}", "not-hex", "1711111111")
    assert not verify_discord_signature(b"{}", "00", "1711111111")
