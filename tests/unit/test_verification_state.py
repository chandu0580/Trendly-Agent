"""The verification lifecycle, and the invariant that the label grants nothing."""

from __future__ import annotations

import json

import pytest

from app.models.conversation import ConversationState, VerificationState

pytestmark = pytest.mark.unit


def fresh() -> ConversationState:
    return ConversationState(session_id="s")


def test_a_new_session_starts_unverified():
    state = fresh()
    assert state.verification_state is VerificationState.UNVERIFIED
    assert not state.is_verified
    assert state.verified_customer_id is None


def test_collecting_an_identifier_authorises_nothing():
    """Seeing "C-100" is not the same as proving it."""
    state = fresh()
    state.mark_identifiers_collected()
    assert state.verification_state is VerificationState.IDENTIFIERS_COLLECTED
    assert not state.is_verified


def test_being_mid_verification_authorises_nothing():
    state = fresh()
    state.mark_verifying()
    assert state.verification_state is VerificationState.VERIFYING
    assert not state.is_verified


def test_a_failure_authorises_nothing_and_leaves_no_identity():
    state = fresh()
    state.mark_verification_failed()
    assert state.verification_state is VerificationState.VERIFICATION_FAILED
    assert not state.is_verified
    assert state.verified_customer_id is None
    assert state.active_order_id is None


def test_only_mark_verified_produces_a_usable_verified_state():
    state = fresh()
    state.mark_verified("C-100", "TR-4524")
    assert state.is_verified
    assert state.verified_customer_id == "C-100"
    assert state.active_order_id == "TR-4524"
    assert state.order_verified is True


def test_setting_the_label_by_hand_does_not_grant_access():
    """The decisive invariant: VERIFIED must be backed by the facts it claims."""
    state = fresh()
    state.verification_state = VerificationState.VERIFIED
    assert not state.is_verified, "the enum alone must never be enough"

    state.verified_customer_id = "C-100"
    assert not state.is_verified, "still missing a verified order"

    state.active_order_id = "TR-4524"
    assert not state.is_verified, "still missing the ownership confirmation"

    state.order_verified = True
    assert state.is_verified


def test_a_later_failure_cannot_revoke_an_established_verification():
    """A mistyped follow-up must not log the customer out mid-conversation."""
    state = fresh()
    state.mark_verified("C-100", "TR-4524")
    state.mark_verification_failed()
    assert state.is_verified
    assert state.verified_customer_id == "C-100"


def test_a_collection_step_cannot_downgrade_a_verified_session():
    state = fresh()
    state.mark_verified("C-100", "TR-4524")
    state.mark_identifiers_collected()
    state.mark_verifying()
    assert state.verification_state is VerificationState.VERIFIED


def test_verification_survives_unrelated_state_changes():
    state = fresh()
    state.mark_verified("C-100", "TR-4524")
    state.messages.append({"role": "user", "content": "hello"})
    state.escalation_status = "ESC-1"
    state.awaiting_item_for = "return"
    assert state.is_verified


# ------------------------------------------- identifiers must come from the customer


def test_a_model_cannot_verify_a_session_with_identifiers_nobody_supplied():
    """Regression: a live run verified a session on turn one, before the customer
    had given anything. The model had lifted a valid pair out of the tool's own
    description. Extraction is the model's job; supplying values is not."""
    from datetime import date

    from app.tools import build_toolset
    from app.tools.context import ToolContext

    ctx = ToolContext.build(
        None, date(2026, 7, 29), customer_utterances="What's the status of my order?"
    )
    tools = {t.name: t for t in build_toolset(ctx)}

    result = tools["verify_identity"].invoke({"customer_id": "C-100", "order_id": "TR-4524"})

    assert result["authorized"] is False
    assert result["reason_code"] == "IDENTIFIERS_NOT_SUPPLIED"
    assert ctx.customer_id is None
    assert tools["get_order"].invoke({"order_id": "TR-4524"})["reason_code"] == "VERIFICATION_REQUIRED"


def test_identifiers_the_customer_did_type_are_accepted():
    from datetime import date

    from app.tools import build_toolset
    from app.tools.context import ToolContext

    ctx = ToolContext.build(
        None, date(2026, 7, 29), customer_utterances="my customer id is C-100 and order TR-4524"
    )
    tools = {t.name: t for t in build_toolset(ctx)}

    result = tools["verify_identity"].invoke({"customer_id": "C-100", "order_id": "TR-4524"})
    assert result["authorized"] is True
    assert ctx.customer_id == "C-100"


def test_only_half_invented_is_still_refused():
    """The customer named their order; the model guessed the customer id."""
    from datetime import date

    from app.tools import build_toolset
    from app.tools.context import ToolContext

    ctx = ToolContext.build(None, date(2026, 7, 29), customer_utterances="Where is TR-4524?")
    tools = {t.name: t for t in build_toolset(ctx)}

    result = tools["verify_identity"].invoke({"customer_id": "C-100", "order_id": "TR-4524"})
    assert result["reason_code"] == "IDENTIFIERS_NOT_SUPPLIED"


def test_the_tool_descriptions_contain_no_usable_identifiers():
    """The values a model is most likely to echo are the ones in its instructions."""
    import re
    from datetime import date

    from app.tools import build_toolset
    from app.tools.context import ToolContext

    valid_orders = {f"TR-45{n}" for n in range(21, 31)}
    for tool in build_toolset(ToolContext.build("C-101", date(2026, 7, 29))):
        text = tool.description + json.dumps(tool.args_schema.model_json_schema())
        assert not re.search(r"\bC-10[0-3]\b", text), f"{tool.name} names a real customer id"
        assert not (valid_orders & set(re.findall(r"\bTR-\d{4}\b", text))), (
            f"{tool.name} names a real order id"
        )
