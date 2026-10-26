from __future__ import annotations

import base64
import hmac
import json
from hashlib import sha256

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from fin_graph.domain.alerts.model import AlertSource
from fin_graph.server.security import WebhookVerifier, canonical_sns_message


@pytest.mark.parametrize("source", list(AlertSource))
def test_each_source_requires_its_own_token(source: AlertSource) -> None:
    verifier = WebhookVerifier(tokens={source.value: "test-token"})
    body = b'{"title":"test"}'
    with pytest.raises(ValueError, match="webhook authentication"):
        verifier.verify(source, body, {}, {"title": "test"})
    assert verifier.verify(
        source, body, {"x-sre-webhook-token": "test-token"}, {"title": "test"}
    ) == {"title": "test"}


def test_hmac_rejects_modified_body_and_token_downgrade() -> None:
    verifier = WebhookVerifier(tokens={"grafana": "token"}, hmac_secrets={"grafana": "secret"})
    body = b'{"title":"test"}'
    signature = hmac.new(b"secret", body, sha256).hexdigest()
    assert verifier.verify(
        AlertSource.GRAFANA, body, {"x-sre-signature": f"sha256={signature}"}, {"title": "test"}
    ) == {"title": "test"}
    with pytest.raises(ValueError, match="webhook authentication"):
        verifier.verify(
            AlertSource.GRAFANA,
            body + b" ",
            {"x-sre-signature": f"sha256={signature}", "x-sre-webhook-token": "token"},
            {"title": "test"},
        )


def test_sns_v2_signature_with_pinned_public_key() -> None:
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_pem = private.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    message = {
        "Type": "Notification",
        "SignatureVersion": "2",
        "MessageId": "m1",
        "Message": json.dumps({"AlarmName": "CPU"}),
        "Timestamp": "2026-10-07T00:00:00Z",
        "TopicArn": "arn:aws:sns:us-east-1:123456789012:alarms",
        "SigningCertURL": "https://sns.us-east-1.amazonaws.com/cert.pem",
    }
    message["Signature"] = base64.b64encode(
        private.sign(canonical_sns_message(message), padding.PKCS1v15(), hashes.SHA256())
    ).decode()
    verifier = WebhookVerifier(
        sns_topic_arn=message["TopicArn"],
        sns_cert_url=message["SigningCertURL"],
        sns_public_key_pem=public_pem,
    )
    assert verifier.verify(AlertSource.CLOUDWATCH, json.dumps(message).encode(), {}, message) == {
        "AlarmName": "CPU"
    }
    message["Message"] = json.dumps({"AlarmName": "tampered"})
    with pytest.raises(ValueError, match="SNS signature"):
        verifier.verify(AlertSource.CLOUDWATCH, json.dumps(message).encode(), {}, message)


def test_malformed_sns_cert_url_is_rejected_without_server_error() -> None:
    verifier = WebhookVerifier(
        sns_topic_arn="arn:aws:sns:us-east-1:1:a",
        sns_cert_url="https://sns.us-east-1.amazonaws.com:invalid/a.pem",
        sns_public_key_pem=b"not-used",
    )
    with pytest.raises(ValueError, match="untrusted SNS certificate URL"):
        verifier.verify(
            AlertSource.CLOUDWATCH,
            b"{}",
            {},
            {
                "Type": "Notification",
                "SignatureVersion": "2",
                "TopicArn": "arn:aws:sns:us-east-1:1:a",
                "SigningCertURL": "https://sns.us-east-1.amazonaws.com:invalid/a.pem",
            },
        )
