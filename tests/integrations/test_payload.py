from __future__ import annotations

import json
from datetime import UTC, datetime

from sre_graph.integrations.payload import PartialList, text_payload, tool_payload


def test_small_data_becomes_compact_json() -> None:
    assert tool_payload({"pods": ["a", "b"], "ok": True}) == '{"pods":["a","b"],"ok":true}'


def test_long_lists_keep_their_head_and_count_the_rest() -> None:
    payload = json.loads(tool_payload({"pods": list(range(120))}, max_items=50))

    assert payload["pods"][:50] == list(range(50))
    assert payload["pods"][50] == "…[70 more items]"
    assert len(payload["pods"]) == 51


def test_nested_lists_are_capped_too() -> None:
    payload = json.loads(tool_payload([{"events": list(range(10))}], max_items=3))

    assert payload == [{"events": [0, 1, 2, "…[7 more items]"]}]


def test_a_list_too_long_for_the_limit_keeps_whole_entries_and_valid_json() -> None:
    payload = tool_payload([{"line": "x" * 90}] * 40, max_chars=1000)

    entries = json.loads(payload)
    kept = len(entries) - 1
    assert 0 < kept < 40
    assert entries[-1] == f"…[{40 - kept} more items]"
    assert len(payload) <= 1000


def test_other_data_over_the_limit_is_cut_with_a_marker() -> None:
    payload = tool_payload({"blob": "x" * 5000}, max_chars=1000)

    assert payload.startswith('{"blob":"xxxx')
    assert payload.endswith("…[truncated 4011 more characters]")


def test_values_json_cannot_encode_are_written_as_text() -> None:
    moment = datetime(2026, 10, 7, 10, 0, tzinfo=UTC)

    assert tool_payload({"at": moment}) == '{"at":"2026-10-07 10:00:00+00:00"}'


def test_short_text_is_returned_unchanged() -> None:
    assert text_payload("line 1\nline 2") == "line 1\nline 2"


def test_long_text_keeps_the_most_recent_whole_lines() -> None:
    text = "\n".join(f"line {number:04d}" for number in range(2000))

    payload = text_payload(text, max_chars=200)

    first, *rest = payload.splitlines()
    assert first.startswith("[…") and first.endswith("earlier characters omitted]")
    assert rest[-1] == "line 1999"
    assert all(line.startswith("line ") and len(line) == 9 for line in rest)


# Built at runtime so this file holds no string that looks like a real credential.
GITHUB_TOKEN = "ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"


def test_secrets_are_redacted_before_the_data_is_serialized() -> None:
    data = {
        "message": json.dumps({"level": "error", "password": "hunter2"}),
        "env": {"DB_PASSWORD": "hunter2"},
    }

    payload = tool_payload(data)

    assert "hunter2" not in payload
    assert json.loads(payload)["env"] == {"DB_PASSWORD": "[REDACTED:assigned_secret]"}


def test_secrets_in_text_are_redacted() -> None:
    assert GITHUB_TOKEN not in text_payload(f"auth failed for {GITHUB_TOKEN}\n")


def test_a_key_cut_by_the_tail_never_leaks_its_body() -> None:
    body = "\n".join("MIIE" + "A" * 60 for _ in range(40))
    key = f"-----BEGIN RSA PRIVATE KEY-----\n{body}\n-----END RSA PRIVATE KEY-----\n"
    text = "boot\n" * 100 + key + "line after the key\n" * 350

    payload = text_payload(text)

    assert "MIIE" not in payload


def test_a_partial_list_says_so_before_its_first_entry() -> None:
    payload = tool_payload(PartialList([{"name": "a"}], note="…[partial: narrow the query]"))

    assert json.loads(payload) == ["…[partial: narrow the query]", {"name": "a"}]
