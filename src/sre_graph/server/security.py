"""Fail-closed webhook verification. SNS uses an operator-pinned public key."""

from __future__ import annotations

import base64
import hmac
import json
import re
from collections.abc import Mapping
from hashlib import sha256
from typing import Any
from urllib.parse import urlparse

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from sre_graph.domain.alerts.model import AlertSource


def canonical_sns_message(message: Mapping[str, Any]) -> bytes:
    """Canonical SNS Notification bytes in AWS's prescribed field order."""
    if message.get("Type") != "Notification":
        raise ValueError("unsupported SNS message type")
    keys = ("Message", "MessageId", "Subject", "Timestamp", "TopicArn", "Type")
    required = set(keys) - {"Subject"}
    if any(not isinstance(message.get(key), str) or not message[key] for key in required):
        raise ValueError("invalid SNS message fields")
    if "Subject" in message and not isinstance(message["Subject"], str):
        raise ValueError("invalid SNS subject")
    return "".join(f"{key}\n{message[key]}\n" for key in keys if key in message).encode()


class WebhookVerifier:
    def __init__(
        self,
        *,
        tokens: Mapping[str, str] | None = None,
        hmac_secrets: Mapping[str, str] | None = None,
        sns_topic_arn: str | None = None,
        sns_cert_url: str | None = None,
        sns_public_key_pem: bytes | None = None,
    ) -> None:
        self.tokens = dict(tokens or {})
        self.hmac_secrets = dict(hmac_secrets or {})
        self.sns_topic_arn = sns_topic_arn
        self.sns_cert_url = sns_cert_url
        self.sns_public_key_pem = sns_public_key_pem

    @property
    def sns_available(self) -> bool:
        return bool(self.sns_topic_arn and self.sns_cert_url and self.sns_public_key_pem)

    def header_authenticated(
        self, source: AlertSource, raw: bytes, headers: Mapping[str, str]
    ) -> bool:
        source_id = source.value
        secret = self.hmac_secrets.get(source_id)
        if secret:
            supplied = headers.get("x-sre-signature", "")
            expected = "sha256=" + hmac.new(secret.encode(), raw, sha256).hexdigest()
            return hmac.compare_digest(supplied, expected)
        token = self.tokens.get(source_id)
        supplied = headers.get("x-sre-webhook-token", "")
        return bool(token and hmac.compare_digest(supplied, token))

    def verify(
        self,
        source: AlertSource,
        raw: bytes,
        headers: Mapping[str, str],
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        if source is AlertSource.CLOUDWATCH and payload.get("Type") in {
            "Notification",
            "SubscriptionConfirmation",
            "UnsubscribeConfirmation",
        }:
            return self._sns(payload)
        if self.header_authenticated(source, raw, headers):
            return payload
        raise ValueError("webhook authentication failed")

    def _sns(self, payload: dict[str, Any]) -> dict[str, Any]:
        if payload.get("Type") != "Notification" or payload.get("SignatureVersion") != "2":
            raise ValueError("unsupported SNS message")
        if not self.sns_topic_arn or payload.get("TopicArn") != self.sns_topic_arn:
            raise ValueError("unexpected SNS topic")
        url = payload.get("SigningCertURL")
        if not isinstance(url, str) or url != self.sns_cert_url or not _valid_sns_url(url):
            raise ValueError("untrusted SNS certificate URL")
        if not self.sns_public_key_pem:
            raise ValueError("SNS public key is not configured")
        try:
            key = serialization.load_pem_public_key(self.sns_public_key_pem)
            if not isinstance(key, rsa.RSAPublicKey):
                raise ValueError("SNS public key must be RSA")
            signature = base64.b64decode(payload["Signature"], validate=True)
            key.verify(
                signature, canonical_sns_message(payload), padding.PKCS1v15(), hashes.SHA256()
            )
            message = json.loads(payload["Message"])
            if not isinstance(message, dict):
                raise ValueError("SNS Message must be a JSON object")
            return message
        except (InvalidSignature, KeyError, TypeError, ValueError) as exc:
            raise ValueError("SNS signature or message invalid") from exc


def _valid_sns_url(value: str) -> bool:
    try:
        url = urlparse(value)
        return (
            url.scheme == "https"
            and not url.username
            and not url.password
            and url.port in (None, 443)
            and bool(re.fullmatch(r"sns\.[a-z0-9-]+\.amazonaws\.com(\.cn)?", url.hostname or ""))
            and url.path.endswith(".pem")
            and not url.query
            and not url.fragment
        )
    except ValueError:
        return False
