"""The support contact reaches the branding payload and the assistant, and only once it is set."""
from unittest.mock import patch

import pytest

from app.agents import prompts
from app.core import support
from app.core.support import get_support_contact


@pytest.fixture
def configured():
    def _set(**fields):
        return patch.dict(support._yaml_config, {"support": fields})
    return _set


def test_nothing_is_configured_by_default(configured):
    with configured(name="", email="", notes=""):
        assert get_support_contact() == {}
        # Unset is stated plainly, so the assistant doesn't go looking for a contact that isn't there.
        unset = prompts._get_support_contact_section()
        assert "No day-to-day support contact has been set up" in unset
        assert "don't invent" in unset and "@" not in unset


def test_only_filled_fields_are_returned_and_trimmed(configured):
    with configured(name="  Data Platform Support ", email="help@example.com", notes=""):
        assert get_support_contact() == {"name": "Data Platform Support", "email": "help@example.com"}


def test_a_malformed_value_is_treated_as_unset():
    with patch.dict(support._yaml_config, {"support": "oops"}):
        assert get_support_contact() == {}


def test_the_assistant_is_given_the_contact(configured):
    with configured(name="Data Platform Support", email="help@example.com", notes="Weekdays 8-5 MT"):
        section = prompts._get_support_contact_section()
    assert "Data Platform Support (help@example.com)" in section
    assert "Weekdays 8-5 MT" in section
    assert "never invent" in section


def test_an_email_alone_is_enough(configured):
    with configured(email="help@example.com"):
        assert "help@example.com" in prompts._get_support_contact_section()


def test_the_prompt_gives_the_contact_when_set_and_says_so_when_not(configured):
    with configured(name="Data Platform Support", email="help@example.com"):
        text = prompts.get_agent_prompt(tools_override=[])
        assert "The contact for day-to-day support is Data Platform Support (help@example.com)" in text
    with configured(name="", email="", notes=""):
        text = prompts.get_agent_prompt(tools_override=[])
        assert "No day-to-day support contact has been set up" in text
        assert "help@example.com" not in text


def test_feedback_guidance_does_not_make_the_user_classify():
    with patch("app.core.feature_flags.is_feature_enabled", return_value=True):
        text = prompts._get_feedback_section()
    assert "data issues" in text
    assert "Never ask the user to classify" in text


@pytest.mark.asyncio
async def test_branding_exposes_the_contact(configured):
    from app.api.v1.branding import get_branding

    with configured(name="Data Platform Support", email="help@example.com"):
        payload = await get_branding()
    assert payload["support_contact"] == {"name": "Data Platform Support", "email": "help@example.com"}
    with configured():
        assert (await get_branding())["support_contact"] == {}
