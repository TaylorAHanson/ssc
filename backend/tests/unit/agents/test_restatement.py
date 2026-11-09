"""collapse_restated_reply: drop a reply's reworded second copy, and nothing else."""
from app.agents.restatement import collapse_restated_reply

FIRST = (
    "I found several existing schemas in `taylor_hanson_build_catalog`, including `main`, "
    "`equipment_center`, and `supply_chain_schema`. Is one of those suitable, or do you need "
    "a separate new schema? If new, tell me the schema name and what it's for; I'll also need "
    "your reason for creating it."
)
SECOND = (
    "I found existing schemas in `taylor_hanson_build_catalog`, including `main`, "
    "`equipment_center`, and `supply_chain_schema`. Would one of those work, or do you need a "
    "separate schema? If you need a new one, share its name and what it's for, plus why you need it."
)


def test_a_reworded_second_copy_is_dropped():
    assert collapse_restated_reply(f"{FIRST} {SECOND}") == FIRST


def test_a_reworded_copy_in_its_own_paragraph_is_dropped():
    assert collapse_restated_reply(f"{FIRST}\n\n{SECOND}") == FIRST


def test_a_single_reply_is_unchanged():
    assert collapse_restated_reply(FIRST) == FIRST


def test_a_short_callback_to_the_opening_is_kept():
    text = (
        f"{FIRST} The schemas I found in `taylor_hanson_build_catalog` include `main`, "
        "`equipment_center`, and `supply_chain_schema`, as listed."
    )
    assert collapse_restated_reply(text) == text


def test_a_reply_with_a_code_block_is_left_alone():
    text = f"{FIRST}\n\n```sql\nCREATE SCHEMA x\n```\n\n{SECOND}"
    assert collapse_restated_reply(text) == text


def test_unrelated_sentences_are_kept():
    text = (
        "The schema `main.sales` exists and you already have read access to it. "
        "Your open request REQ-12 is waiting on an Application Admin. "
        "Approvals usually take a day, and you'll get an email when it's done."
    )
    assert collapse_restated_reply(text) == text


def test_empty_text_is_unchanged():
    assert collapse_restated_reply("") == ""
