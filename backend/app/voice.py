from enum import Enum
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, field_validator

from .menu import MENU, MAX_NOTES_PER_ITEM, sanitize_note


class VoiceMode(str, Enum):
    wake = "wake"
    normal = "normal"
    blind = "blind"


class VoiceAction(str, Enum):
    select_category = "select_category"
    add_item = "add_item"
    set_quantity = "set_quantity"
    remove_item = "remove_item"
    add_note = "add_note"
    remove_note = "remove_note"
    finish_customization = "finish_customization"
    continue_ordering = "continue_ordering"
    review_order = "review_order"
    confirm_order = "confirm_order"
    end_session = "end_session"
    activate_blind_mode = "activate_blind_mode"


CATEGORIES = {
    "mains": ["burger", "pizza"],
    "breakfast": ["pancakes", "toast"],
    "bowls": ["corn", "salad"],
    "drinks": ["iced-tea", "lemonade", "coffee"],
}
CATEGORY_ALIASES = {"food": "mains", "main": "mains", "beverages": "drinks", "drink": "drinks", "bowl": "bowls"}


class VoiceState(BaseModel):
    category: str | None = None
    items: list[dict[str, Any]] = Field(default_factory=list)
    screen: Literal["menu", "preferences", "checkout", "submit_pending", "ended", "blind_wake"] = "menu"
    active_item_id: str | None = None


class VoiceActionEnvelope(BaseModel):
    schema_version: Literal["voice-action.v1"] = "voice-action.v1"
    event_id: UUID = Field(default_factory=uuid4)
    session_id: UUID
    mode: VoiceMode
    action: VoiceAction
    payload: dict[str, Any] = Field(default_factory=dict)
    state: VoiceState


class LiveContext(BaseModel):
    active_item_id: str | None = None
    order: list[dict[str, Any]] | None = None

    @field_validator("active_item_id")
    @classmethod
    def valid_item(cls, value):
        if value is not None and value not in MENU:
            raise ValueError("unknown active item")
        return value


def canonical_category(value: Any) -> str:
    category = str(value or "").lower().strip().replace(" ", "-")
    category = CATEGORY_ALIASES.get(category, category)
    if category not in CATEGORIES:
        raise ValueError("unknown category")
    return category


def menu_context(category: str | None = None):
    ids = CATEGORIES.get(category, list(MENU)) if category else list(MENU)
    return {"categories": CATEGORIES, "items": {iid: {"name": MENU[iid]["name"]} for iid in ids}}


def _append_note_unique(prefs: list[str], note: str):
    if note.casefold() not in {p.casefold() for p in prefs}:
        prefs.append(note)


class LiveCart:
    def __init__(self, context: LiveContext | None = None):
        self.category: str | None = None
        self.screen: Literal["menu", "preferences", "checkout", "submit_pending", "ended", "blind_wake"] = "menu"
        self.active_item_id = context.active_item_id if context else None
        self.items: dict[str, dict[str, Any]] = {}
        for row in (context.order if context else None) or []:
            iid = row.get("id") if isinstance(row, dict) else None
            qty = row.get("quantity") if isinstance(row, dict) else None
            if iid in MENU and isinstance(qty, int) and 1 <= qty <= 20:
                prefs = []
                for pref in row.get("preferences", [])[:MAX_NOTES_PER_ITEM]:
                    _append_note_unique(prefs, sanitize_note(pref))
                self.items[iid] = {"id": iid, "quantity": qty, "preferences": prefs}

    def snapshot(self) -> VoiceState:
        return VoiceState(category=self.category, items=list(self.items.values()), screen=self.screen, active_item_id=self.active_item_id)

    def total_qty_without(self, item_id: str | None = None) -> int:
        return sum(v["quantity"] for k, v in self.items.items() if k != item_id)

    def apply(self, action: VoiceAction, payload: dict[str, Any]):
        if action == VoiceAction.activate_blind_mode:
            self.screen = "blind_wake"; return
        if action == VoiceAction.select_category:
            self.category = payload["category"]; self.screen = "menu"; return
        if action in {VoiceAction.add_item, VoiceAction.set_quantity}:
            iid = payload["item_id"]; qty = payload.get("quantity", 1)
            prefs = self.items.get(iid, {"preferences": []})["preferences"]
            self.items[iid] = {"id": iid, "quantity": qty, "preferences": prefs}; self.active_item_id = iid; self.screen = "preferences"; return
        if action == VoiceAction.remove_item:
            self.items.pop(payload["item_id"], None); self.screen = "menu"; return
        if action in {VoiceAction.add_note, VoiceAction.remove_note}:
            iid = payload["item_id"]
            if iid not in self.items: self.items[iid] = {"id": iid, "quantity": 1, "preferences": []}
            prefs = self.items[iid]["preferences"]
            note = payload["note"]
            if action == VoiceAction.add_note:
                if note.casefold() in {p.casefold() for p in prefs}: return
                if len(prefs) >= MAX_NOTES_PER_ITEM: raise ValueError("too many notes")
                _append_note_unique(prefs, note)
            else:
                self.items[iid]["preferences"] = [p for p in prefs if p != note]
            self.active_item_id = iid; self.screen = "preferences"; return
        if action in {VoiceAction.finish_customization, VoiceAction.continue_ordering}:
            self.screen = "menu"; return
        if action == VoiceAction.review_order:
            if not self.items: raise ValueError("empty cart")
            self.screen = "checkout"; return
        if action == VoiceAction.confirm_order:
            self.screen = "submit_pending"; return
        if action == VoiceAction.end_session:
            self.screen = "ended"; return


def validate_payload(name: str, args: dict[str, Any] | None, *, mode: VoiceMode, context: LiveContext | None = None) -> tuple[VoiceAction, dict[str, Any]]:
    args = args or {}; action = VoiceAction(name)
    if mode == VoiceMode.wake and action != VoiceAction.activate_blind_mode: raise ValueError("action not allowed in wake mode")
    if mode == VoiceMode.normal and action not in {VoiceAction.add_note, VoiceAction.remove_note, VoiceAction.finish_customization, VoiceAction.end_session}: raise ValueError("action not allowed in normal mode")
    payload: dict[str, Any] = {}
    item_id = args.get("item_id") or (context.active_item_id if context else None)
    if action in {VoiceAction.add_item, VoiceAction.set_quantity, VoiceAction.remove_item, VoiceAction.add_note, VoiceAction.remove_note}:
        if item_id not in MENU: raise ValueError("unknown item id")
        payload["item_id"] = item_id
    if action == VoiceAction.select_category: payload["category"] = canonical_category(args.get("category"))
    if action in {VoiceAction.add_item, VoiceAction.set_quantity}:
        if "quantity" not in args and action == VoiceAction.set_quantity: raise ValueError("quantity required")
        qty = args.get("quantity", 1)
        if not isinstance(qty, int) or qty < 1 or qty > 20: raise ValueError("invalid quantity")
        payload["quantity"] = qty
    if action in {VoiceAction.add_note, VoiceAction.remove_note}: payload["note"] = sanitize_note(args.get("note"))
    return action, payload


def make_envelope(*, session_id: UUID, mode: VoiceMode, action: VoiceAction, payload: dict[str, Any], cart: LiveCart) -> VoiceActionEnvelope:
    return VoiceActionEnvelope(session_id=session_id, mode=mode, action=action, payload=payload, state=cart.snapshot())


def normalize_tool_action(name: str, args: dict[str, Any] | None, *, mode: VoiceMode, session_id: UUID, context: LiveContext | None = None) -> VoiceActionEnvelope:
    cart = LiveCart(context); action, payload = validate_payload(name, args, mode=mode, context=context); cart.apply(action, payload)
    return make_envelope(session_id=session_id, mode=mode, action=action, payload=payload, cart=cart)


def tool_declarations():
    empty = {"type": "object", "properties": {}}
    item = {"type": "object", "properties": {"item_id": {"type": "string", "enum": list(MENU)}}, "required": ["item_id"]}
    qty = {"type": "object", "properties": {"item_id": {"type": "string", "enum": list(MENU)}, "quantity": {"type": "integer", "minimum": 1, "maximum": 20}}, "required": ["item_id", "quantity"]}
    add_item = {"type": "object", "properties": {"item_id": {"type": "string", "enum": list(MENU)}, "quantity": {"type": "integer", "minimum": 1, "maximum": 20}}, "required": ["item_id"]}
    note = {"type": "object", "properties": {"item_id": {"type": "string", "enum": list(MENU)}, "note": {"type": "string", "maxLength": 160}}, "required": ["item_id", "note"]}
    category = {"type": "object", "properties": {"category": {"type": "string", "enum": list(CATEGORIES)}}, "required": ["category"]}
    schemas = {
        VoiceAction.select_category: category, VoiceAction.add_item: add_item, VoiceAction.set_quantity: qty,
        VoiceAction.remove_item: item, VoiceAction.add_note: note, VoiceAction.remove_note: note,
        VoiceAction.finish_customization: empty, VoiceAction.continue_ordering: empty,
        VoiceAction.review_order: empty, VoiceAction.confirm_order: empty, VoiceAction.end_session: empty,
        VoiceAction.activate_blind_mode: empty,
    }
    return [{"function_declarations": [{"name": a.value, "description": f"Emit {a.value} kiosk action", "parameters": schemas[a]} for a in VoiceAction]}]


def system_prompt(mode: VoiceMode, context: LiveContext | None = None) -> str:
    base = "Use only provided tools for all cart/order mutations. Never invent item ids, names, prices, or unavailable category items."
    if mode == VoiceMode.wake: return 'Silently listen. Call only activate_blind_mode upon the exact phrase "HEY KAISIGN". No audio/text otherwise.'
    initial = LiveCart(context).snapshot().model_dump(mode="json")
    ctx = " Canonical current context (no prices): " + str(initial) + "."
    if mode == VoiceMode.normal: return base + ctx + " Accept spoken preferences only for active_item_id; emit add_note/remove_note tools only using that canonical item id; use finish_customization when preferences are declined or complete."
    return base + ctx + ' Your first audible response must be a short professional greeting followed by exactly: "What would you like to order?" select_category uses only mains, breakfast, bowls, drinks and after category selection speak only items in that category. After add_item, ask whether the user wants preferences for that item. If preferences are declined or complete, call finish_customization/continue_ordering and invite another item or an explicit review request. Never call review_order unless the user explicitly asks to review or checkout and the cart is non-empty. review_order shows checkout. Never call confirm_order unless the user gives final affirmative confirmation after review. Do not auto-review because an item was added.'
