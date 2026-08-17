import sys
import types

import pytest

dotenv = types.ModuleType("dotenv")
setattr(dotenv, "load_dotenv", lambda *args, **kwargs: None)
sys.modules.setdefault("dotenv", dotenv)

from app.live import is_terminal_wake_activation, maybe_send_blind_opening_turn, release_live_connection, reserve_live_connection
from app.voice import BLIND_OPENING_GREETING, WAKE_PHRASE, LiveCart, VoiceAction, VoiceMode, menu_context, system_prompt


def test_wake_prompt_uses_new_canonical_phrase():
    prompt = system_prompt(VoiceMode.wake)

    assert WAKE_PHRASE == "Hey Kaizen"
    assert f'"{WAKE_PHRASE}"' in prompt


def test_wake_prompt_does_not_retain_stale_phrase():
    assert "KAISIGN" not in system_prompt(VoiceMode.wake)


def test_terminal_wake_action_is_unchanged():
    assert is_terminal_wake_activation(VoiceMode.wake, "activate_blind_mode") is True
    assert is_terminal_wake_activation(VoiceMode.blind, "activate_blind_mode") is False


def test_blind_prompt_requires_exact_greeting_and_conversation():
    prompt = system_prompt(VoiceMode.blind)

    assert f'"{BLIND_OPENING_GREETING}"' in prompt
    assert BLIND_OPENING_GREETING == "Hello, welcome to Kaizen. We have mains, breakfast, bowls, and drinks. Which category or item would you like to order?"
    assert "natural spoken turn-by-turn ordering conversation" in prompt
    assert "End the session only when the user explicitly asks" in prompt


def test_menu_context_exposes_canonical_names_and_prices():
    menu = menu_context("mains")

    assert menu["items"]["burger"] == {"name": "House Burger", "price": "$11.50", "price_cents": 1150}
    assert menu["items"]["pizza"] == {"name": "Margherita Pizza", "price": "$12.75", "price_cents": 1275}


def test_checkout_summary_uses_server_canonical_totals_and_spoken_readback():
    cart = LiveCart()
    cart.apply(VoiceAction.add_item, {"item_id": "burger", "quantity": 2})
    cart.apply(VoiceAction.add_note, {"item_id": "burger", "note": "No onions"})
    cart.apply(VoiceAction.add_item, {"item_id": "coffee", "quantity": 1})

    summary = cart.checkout_summary()

    assert summary["items"][0]["name"] == "House Burger"
    assert summary["items"][0]["unit_price_cents"] == 1150
    assert summary["subtotal_cents"] == 2625
    assert summary["tax_cents"] == 217
    assert summary["total_cents"] == 2842
    assert "2 House Burger with preferences: No onions" in summary["spoken_summary"]
    assert "1 Coffee with no preferences" in summary["spoken_summary"]
    assert "Subtotal $26.25. Tax $2.17. Total $28.42." in summary["spoken_summary"]


def test_blind_prompt_requires_menu_narration_and_full_checkout_readback():
    prompt = system_prompt(VoiceMode.blind)

    assert "state the available categories" in prompt
    assert "read the selected category items with their canonical prices" in prompt
    assert "narrate categories, items, and prices" in prompt
    assert "speak checkout_summary.spoken_summary verbatim and completely" in prompt
    assert "before asking for yes/no final confirmation" in prompt


@pytest.mark.asyncio
async def test_opening_turn_uses_exact_greeting():
    class FakeSession:
        def __init__(self):
            self.turns = None

        async def send_client_content(self, **kwargs):
            self.turns = kwargs["turns"]

    class FakeTypes:
        class Content:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

        class Part:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

    session = FakeSession()

    assert await maybe_send_blind_opening_turn(session, FakeTypes, VoiceMode.blind) is True
    assert session.turns is not None
    part = session.turns.kwargs["parts"][0]
    assert BLIND_OPENING_GREETING in part.kwargs["text"]


@pytest.mark.asyncio
async def test_released_terminal_wake_reservation_allows_immediate_blind_handoff():
    token = await reserve_live_connection("wake-handoff-test")
    assert token

    await release_live_connection("wake-handoff-test", token)
    blind_token = await reserve_live_connection("wake-handoff-test")

    assert blind_token
    await release_live_connection("wake-handoff-test", blind_token)


@pytest.mark.asyncio
async def test_late_old_release_does_not_release_replacement_connection():
    old_token = await reserve_live_connection("wake-handoff-ownership-test")
    assert old_token
    await release_live_connection("wake-handoff-ownership-test", old_token)
    replacement_token = await reserve_live_connection("wake-handoff-ownership-test")
    assert replacement_token

    await release_live_connection("wake-handoff-ownership-test", old_token)

    assert await reserve_live_connection("wake-handoff-ownership-test") is None
    await release_live_connection("wake-handoff-ownership-test", replacement_token)
