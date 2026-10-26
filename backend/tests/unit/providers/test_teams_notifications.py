"""Teams Workflows webhook messages: Adaptive Card envelope, @mentions, HTTP."""
import json
from datetime import datetime

import httpx
import pytest

from app.core import config as cfg
from app.providers.notifications import teams
from app.services import context_catalog_staleness as staleness


def _stale(doc_id, title, owner, age=120, domain="Ops"):
    return staleness.StaleDocument(
        id=doc_id, title=title, domain_id="dom-1", domain_name=domain, owner=owner,
        owner_source="document" if owner else None, last_activity_at=datetime(2026, 6, 1),
        age_days=age, url=f"https://app.example/governance/context-catalog?domain=dom-1&doc={doc_id}",
        stale_notified_at=None,
    )


@pytest.fixture(autouse=True)
def _settings(monkeypatch):
    monkeypatch.setattr(cfg.settings, "APP_BASE_URL", "https://app.example")
    monkeypatch.setattr(cfg.settings, "CONTEXT_CATALOG_TEAMS_WEBHOOK_URL", "https://hook.example/wf")


@pytest.fixture
def transport(monkeypatch):
    """Route post_message through an httpx MockTransport and record requests."""
    seen = []
    status = {"code": 202}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status["code"], text="" if status["code"] < 300 else "bad card")

    real_client = httpx.Client
    monkeypatch.setattr(
        teams.httpx, "Client",
        lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw),
    )
    return seen, status


def test_reminder_card_shape_and_mentions():
    docs = [
        _stale("d1", "Pricing playbook", "jane.doe@corp.com"),
        _stale("d2", "Escalations", "jane.doe@corp.com", age=95),
        _stale("d3", "Onboarding", None),
    ]
    messages = staleness.build_reminder_messages(staleness.group_by_owner(docs), days=90)

    assert len(messages) == 1
    msg = messages[0].payload
    assert msg["type"] == "message"
    attachment = msg["attachments"][0]
    assert attachment["contentType"] == "application/vnd.microsoft.card.adaptive"
    card = attachment["content"]
    assert card["type"] == "AdaptiveCard" and card["version"] == "1.5"
    assert card["$schema"] == "http://adaptivecards.io/schemas/adaptive-card.json"

    # Exactly one mention for the one email owner, and its text appears in the body.
    entities = card["msteams"]["entities"]
    assert entities == [{
        "type": "mention",
        "text": "<at>Jane Doe</at>",
        "mentioned": {"id": "jane.doe@corp.com", "name": "Jane Doe"},
    }]
    body_text = "\n".join(b.get("text", "") for b in card["body"])
    assert body_text.count("<at>Jane Doe</at>") == 1
    assert "2 documents to review" in body_text
    assert "[Pricing playbook](https://app.example/governance/context-catalog?domain=dom-1&doc=d1)" in body_text
    assert "**No owner**" in body_text
    assert card["actions"][0]["url"] == "https://app.example/governance/context-catalog"
    assert set(messages[0].document_ids) == {"d1", "d2", "d3"}


def test_titles_cannot_forge_mentions_or_links():
    docs = [_stale("d1", "<at>Jane Doe</at> [click](https://evil.example)", "bob@corp.com")]
    card = staleness.build_reminder_messages(staleness.group_by_owner(docs))[0].payload
    body_text = "\n".join(b.get("text", "") for b in card["attachments"][0]["content"]["body"])
    assert "<at>Jane Doe</at>" not in body_text
    assert "](https://evil.example)" not in body_text


def test_long_lists_are_capped_per_owner():
    docs = [_stale(f"d{i}", f"Doc {i}", "jane@corp.com") for i in range(5)]
    msg = staleness.build_reminder_messages(staleness.group_by_owner(docs), per_owner_limit=2)[0]
    body_text = "\n".join(b.get("text", "") for b in msg.payload["attachments"][0]["content"]["body"])
    assert "…and 3 more" in body_text
    assert len(msg.document_ids) == 2


def test_large_reminders_split_by_owner_under_the_size_cap():
    docs = [_stale(f"d{i}", f"Document number {i} " + "x" * 80, f"owner{i}@corp.com") for i in range(12)]
    messages = staleness.build_reminder_messages(staleness.group_by_owner(docs), max_bytes=4000)

    assert len(messages) > 1
    owners = [o for m in messages for o in m.owners]
    assert sorted(owners) == sorted(f"owner{i}@corp.com" for i in range(12))  # each exactly once
    for i, m in enumerate(messages, start=1):
        card = m.payload["attachments"][0]["content"]
        assert f"(part {i} of {len(messages)})" in card["body"][0]["text"]
        # Each part only carries entities for the owners it mentions.
        assert {e["mentioned"]["id"] for e in card["msteams"]["entities"]} == set(m.owners)


def test_post_message_sends_json_and_treats_2xx_as_success(transport):
    seen, _ = transport
    payload = teams.adaptive_card_message([{"type": "TextBlock", "text": "hi"}])
    result = teams.post_message("https://hook.example/wf", payload)
    assert result.ok and result.status_code == 202
    assert str(seen[0].url) == "https://hook.example/wf"
    assert json.loads(seen[0].content) == payload


def test_post_message_reports_http_errors(transport):
    _, status = transport
    status["code"] = 400
    result = teams.post_message("https://hook.example/wf", {"type": "message"})
    assert not result.ok and result.status_code == 400 and "bad card" in result.error


def test_post_message_without_url_does_not_call_out(transport):
    seen, _ = transport
    assert not teams.post_message("", {"type": "message"}).ok
    assert seen == []


def test_send_test_message_mentions_the_requesting_admin(transport):
    seen, _ = transport
    result = staleness.send_test_message("pat.admin@corp.com")
    assert result.ok
    card = json.loads(seen[0].content)["attachments"][0]["content"]
    assert card["msteams"]["entities"][0]["mentioned"]["id"] == "pat.admin@corp.com"
    assert "<at>Pat Admin</at>" in card["body"][1]["text"]


def test_webhook_url_prefers_env_then_secret(monkeypatch):
    monkeypatch.setattr(teams, "_read_secret", lambda scope, key: f"https://secret/{scope}/{key}")
    assert teams.resolve_webhook_url(" https://env/hook ", "scope", "key") == "https://env/hook"
    assert teams.resolve_webhook_url("", "scope", "key") == "https://secret/scope/key"
    assert teams.resolve_webhook_url("", "", "key") == ""
    assert teams.webhook_source("", "scope", "key") == "secret scope/key"
