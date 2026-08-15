import re
from decimal import Decimal, ROUND_HALF_UP

MENU = {
    "burger": {"name": "House Burger", "price": Decimal("11.50")},
    "pancakes": {"name": "Stack Pancakes", "price": Decimal("9.25")},
    "pizza": {"name": "Margherita Pizza", "price": Decimal("12.75")},
    "corn": {"name": "Corn Bowl", "price": Decimal("8.50")},
    "toast": {"name": "Sweet Toast", "price": Decimal("7.75")},
    "salad": {"name": "Market Salad", "price": Decimal("10.25")},
    "iced-tea": {"name": "Iced Tea", "price": Decimal("3.50")},
    "lemonade": {"name": "Lemonade", "price": Decimal("4.00")},
    "coffee": {"name": "Coffee", "price": Decimal("3.25")},
}
TAX_RATE = Decimal("0.0825")
VALID_PREFERENCES = {
    "Extra cheese", "Less cheese", "Double cheese", "No cheese", "Add cheese",
    "Extra butter", "Less butter", "Double butter", "No butter", "Add butter",
    "Extra sugar", "Less sugar", "Double sugar", "No sugar", "Add sugar",
    "Extra salt", "Less salt", "Double salt", "No salt", "Add salt",
}
MAX_NOTES_PER_ITEM = 10
MAX_NOTE_CHARS = 160

def sanitize_note(value):
    if not isinstance(value, str):
        raise ValueError("invalid note")
    note = re.sub(r"\s+", " ", value).strip()
    note = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", note)
    if not note or len(note) > MAX_NOTE_CHARS:
        raise ValueError("invalid note")
    return note

def normalize_preferences(prefs):
    if not isinstance(prefs, list) or len(prefs) > MAX_NOTES_PER_ITEM:
        raise ValueError("invalid preference")
    normalized = []
    for pref in prefs:
        if pref in VALID_PREFERENCES:
            normalized.append(pref)
        else:
            try:
                normalized.append(sanitize_note(pref))
            except ValueError as exc:
                raise ValueError("invalid preference") from exc
    return normalized

def cents(d: Decimal) -> int:
    return int((d * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))

def validate_and_total(items):
    if not isinstance(items, list) or not items:
        raise ValueError("items must be a non-empty list")
    normalized = []
    subtotal = Decimal("0")
    for item in items:
        iid = item.get("id") if isinstance(item, dict) else None
        if iid not in MENU:
            raise ValueError("unknown item id")
        qty = item.get("quantity")
        if not isinstance(qty, int) or qty < 1 or qty > 20:
            raise ValueError("invalid quantity")
        prefs = normalize_preferences(item.get("preferences", []))
        row = MENU[iid]
        line = row["price"] * qty
        subtotal += line
        normalized.append({"id": iid, "name": row["name"], "quantity": qty, "unit_price_cents": cents(row["price"]), "preferences": prefs, "line_total_cents": cents(line)})
    tax = (subtotal * TAX_RATE).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return {"items": normalized, "subtotal_cents": cents(subtotal), "tax_cents": cents(tax), "total_cents": cents(subtotal + tax)}
