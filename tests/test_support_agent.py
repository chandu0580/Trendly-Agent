from app.agent import SupportAgent


AS_OF = "2026-08-24"


def ask(agent, session, customer, message):
    return agent.respond(session, customer, message, AS_OF)


def test_happy_path_return_requires_confirmation_then_creates():
    agent = SupportAgent()
    reply, actions, _, _ = ask(agent, "return-happy", "C-101", "I want to return TR-4530")
    assert "eligible" in reply.lower()
    assert not actions
    reply, actions, _, _ = ask(agent, "return-happy", "C-101", "confirm")
    assert "created" in reply.lower()
    assert actions[0]["type"] == "return_created"


def test_lost_parcel_is_escalated_not_returned():
    agent = SupportAgent()
    reply, actions, handoff, _ = ask(agent, "lost", "C-101", "Where is order TR-4526? Can I return it?")
    assert "lost" in reply.lower()
    assert actions[0]["type"] == "escalated"
    assert "Lost-parcel claim" in handoff


def test_non_returnable_jewellery_refused_inside_window():
    agent = SupportAgent()
    reply, actions, _, _ = agent.respond("jewellery", "C-102", "return TR-4527", "2026-08-01")
    assert "non-returnable" in reply.lower()
    assert not actions


def test_final_sale_is_exchange_only():
    agent = SupportAgent()
    reply, actions, _, _ = agent.respond("final-sale", "C-103", "return TR-4528", "2026-08-01")
    assert "size exchange only" in reply.lower()
    assert not actions


def test_cross_customer_order_does_not_leak():
    agent = SupportAgent()
    reply, actions, _, _ = ask(agent, "privacy", "C-100", "What is the status of TR-4526?")
    assert "no policy-backed answer" not in reply.lower()
    assert "delhivery" not in reply.lower()
    assert "order id" in reply.lower() or "matching order" in reply.lower()
    assert not actions


def test_partial_shipment_explains_backorder_without_extra_charge():
    agent = SupportAgent()
    reply, _, _, _ = ask(agent, "partial", "C-100", "Track TR-4524")
    assert "partially shipped" in reply.lower()
    assert "no extra shipping cost" in reply.lower()


def test_address_change_after_dispatch_is_explained():
    agent = SupportAgent()
    reply, _, _, _ = ask(agent, "address", "C-100", "Please change address for TR-4521")
    assert "can’t be changed" in reply.lower()
    assert "refuse delivery" in reply.lower()


def test_cancelled_order_reports_refund_instead_of_creating_return():
    agent = SupportAgent()
    reply, actions, _, _ = ask(agent, "cancelled", "C-100", "What is the refund for TR-4529?")
    assert "refund status is processed" in reply.lower()
    assert not actions


def test_sensitive_financial_data_is_refused():
    agent = SupportAgent()
    reply, actions, _, _ = ask(agent, "bank", "C-103", "My bank account number is 1234567890123456 for COD refund")
    assert "don’t share" in reply.lower() or "do not share" in reply.lower()
    assert not actions


def test_unknown_policy_question_gets_human_handoff():
    agent = SupportAgent()
    reply, actions, handoff, _ = ask(agent, "unknown", "C-100", "Do you offer gift wrapping?")
    assert "don’t have a policy-backed answer" in reply.lower()
    assert actions[0]["type"] == "escalated"
    assert handoff
