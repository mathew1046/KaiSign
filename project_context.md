# Project Context — Interactive ASL Sign for Restaurant Ordering

> **Competition:** Seeed Studio "Make a Sign" Interactive Signage Contest 2026
> **Concept:** An accessible restaurant ordering sign for deaf users. The customer
> picks dish(es), customizes each with American Sign Language (ASL) gestures captured
> by a camera, then checks out.
>
> **Core UX principle:** *Deaf diners order in their own language.* The sign shows the
> menu visually; customization is done by signing, not by hunting through touch menus.

---

## 1. Product Overview

### 1.1 What it does
- A **touchscreen sign** (plus camera) placed in a restaurant.
- Customer **browses and selects dish(es)** by tapping.
- For each selected dish, they enter a **customization screen** where they express
  edits by **making ASL hand signs** in front of the camera.
- The system recognizes the sign, applies the modification, and the customer confirms.
- Finally a **checkout / review** screen shows the complete order before sending it to
  the kitchen.

### 1.2 The 10 core ASL signs (v1)
We have a deployed model that recognises these 10 signs:

| # | Sign    | Meaning in context                     |
|---|---------|----------------------------------------|
| 1 | MORE    | "Add more" of the highlighted ingredient|
| 2 | LESS    | "Less of" the highlighted ingredient    |
| 3 | DOUBLE  | Double the portion/piece of the item    |
| 4 | CHEESE  | Add/remove cheese                      |
| 5 | BUTTER  | Add/remove butter                      |
| 6 | SUGAR   | Add/remove sugar                       |
| 7 | WITHOUT | Remove / hold the highlighted item      |
| 8 | ADD     | Add the highlighted ingredient          |
| 9 | NO      | "No ___" — a hard exclusion            |
| 10| SALT    | Add/remove salt                        |

**Design implication:** Each customization target (ingredient) is paired with action
signs. The two-part grammar is: **target + action**.
- e.g. show ingredient **CHEESE**, user signs **MORE** → "more cheese"
- show ingredient **CHEESE**, user signs **NO / WITHOUT** → "no cheese / without cheese"

---

## 2. User Workflow (the 3 main screens)

```
 ┌────────────┐     ┌─────────────────────┐     ┌───────────────┐
 │ 1. DISH     │ ──► │ 2. CUSTOMIZATION     │ ──► │ 3. CHECKOUT   │
 │ SELECTION   │     │ (per selected dish)  │     │ (order review)│
 └────────────┘     └─────────────────────┘     └───────────────┘
   tap dishes        sign to customize           review & confirm
   (multi-select)    next">Confirm & Send to Kitchen
```

### Screen 1 — Dish Selection
- Grid of food items with photos + names + prices.
- User taps to select; selected items get a highlighted border + count badge.
- User can select **multiple** dishes.
- A persistent cart/order bar shows running total and a **"Next →"** / cart button.
- Tapping an already-selected dish de-selects it (or opens its customizations).

### Screen 2 — Customization (per dish)
- Header: which dish this customization belongs to (photo + name).
- The current customization **target** is displayed large and central (e.g. a big cheese icon with the word "CHEESE").
- A **live camera preview** shows the user's hand (feedback loop so they can see the sign being tracked).
- The **recognized sign result** appears as text + icon (e.g. "MORE CHEESE").
- Confirmation button ("Looks good → next ingredient" / "next →").
- The dish select on by default; navigation lets them go through ingredients one at a
  time, or a list lets them jump to a specific one.
- Ability to go **back to dish selection** or **forward to checkout**.
- Show what's been changed so far (an "order so far" chip trailer).

### Screen 3 — Checkout / Review
- Full order summary grouped by dish.
- Each dish lists its customizations (e.g. "Burger — more cheese, no onion").
- Running total + tax.
- **"Send to Kitchen"** (final) and **"Back to edit"** buttons.
- Clear success/confirmation state after sending, with a "New Order" reset.

---

## 3. UI Design Requirements & Components

### 3.1 General style
- **High contrast, large type** — designed for glanceability in a bright restaurant and
  for accessibility.
- **Icon-first** — every item shown with a clear icon/photo + text label (helps users
  who may have limited literacy as well as hearing).
- **Color-coded states:** neutral, selected, confirmed, error, disabled.
- Video-safe: UI should read clearly both on-device and in a 1+ minute demo video.

### 3.2 Reusable components
- **DishCard** — image, name, price, +/select state, quantity badge.
- **CartBar** — item count, running total, "Next / Checkout" CTA.
- **IngredientSelector** — the central large target the user signs about, with an icon +
  label, and a selected/unselected highlight.
- **CameraPreview** — live hand-tracking window (with a subtle frame/border showing it's
  active).
- **SignResultChip** — shows the recognized sign as text + icon after recognition
  (e.g. a thumbs-up-ish badge for MORE).
- **NextButton / BackButton** — large primary/secondary action buttons.
- **ProgressIndicator** — shows "dish 2 of 3" or a step stepper (Select → Customize →
  Checkout).
- **Toast/Confirmation overlay** — transient "✓ Added: more cheese".

### 3.3 Sign-action ↔ UI mapping
When a sign is recognized, the UI should respond instantly and clearly:

| Sign | UI action example |
|------|-------------------|
| ADD | highlight ingredient → mark as "added", show "+ ADDED" |
| MORE | increment a quantity ("+1 more cheese") |
| LESS | decrement / set to "light" |
| NO / WITHOUT | mark ingredient as excluded (e.g. strikethrough or "NO ✓") |
| DOUBLE | set quantity ×2 for the item |
| CHEESE/BUTTER/SUGAR/SALT | select that ingredient as the active target, or add it |

Every recognition should be **confirmable** before moving on, to prevent mistakes.

---

## 4. State / Data Model (UI backing)

```
Menu (static)
 └─ Dish { id, name, image, basePrice, category, defaultIngredients[] }

OrderSession (runtime)
 ├─ items[] = DishItem[]
 ├─       DishItem { dish: Dish, qty, customizations[] }
 │─                     customizations[] = Modification {
 │                          target: ingredient  (cheese/butter/sugar/salt/onion/…)
 │                          action: ADD | MORE | LESS | NO | WITHOUT | DOUBLE
 │                          qty?  }
 └─ total
```

- The UI consumes an `OrderSession` and re-renders on change.
- The **camera/sign-recognition layer** should publish recognized sign events
  (`onSignDetected(sign)`) which the UI consumes to mutate customizations.

---

## 5. Sign-Recognition Integration (for the UI dev)

- Model is a **Random Forest** trained on **10 ASL signs** from MediaPipe hand landmarks.
- Input: `(T, 21, 4)` landmark sequence → predicted sign + confidence.
- The UI exposes a **live camera feed**; recognized labels are emitted via a callback.
- Confidence threshold recommended (~0.5–0.6) to filter weak/ambiguous reads; below
  threshold → show "please sign again" state.
- **Important:** map the model's 10 classes to a friendly display name + icon at the UI
  boundary (never show raw class strings like "a" or indices).

---

## 6. Screen-by-Screen Build Plan (implementation order)

1. **Design system** — colors, type scale, icon set, reusable buttons/cards.
2. **Screen 1 — Dish grid + cart bar** (static menu data first).
3. **Screen 2 — Customization flow** (start with mock/tap-based "signs" so the flow works
   without the camera; then wire live recognition).
4. **Screen 3 — Checkout & order review**.
5. **Live camera integration** — swap mock signs for real ASL recognition, add
   confidence gating + debounce + confirm step.
6. **Polish** — progress stepper, toasts, edge cases (empty cart, single-instance dish).
7. **Demo video pass** — ensure every screen is legible in the contest video.

---

## 7. Edge Cases & UX Decisions to Plan For

- **No hand visible** → show "place your hand in frame" hint + dim/disable INPUT state.
- **Low confidence** → ask user to repeat ("sorry, please sign again").
- **Sign ambiguity** (e.g. ADD vs MORE when adding an ingredient) → bind each target to
  clear, distinct actions; show preview of what will happen before confirming.
- **No changes needed** → user should still be able to skip customization to checkout.
- **Multiple dishes** → clear step indicator so user knows they're on "Burger, dish 2/3".
- **Empty selection** → disable "Next" until at least one dish is chosen.
- **Back / reset** → clear path "back" and a way to reset the whole order.

---

## 8. Deliverables Defined by This Document
- UI wireframes for the 3 screens (this doc is the contract for those).
- A working **OrderSession** state model the UI renders.
- A **sign→action** mapping table for all 10 signs.
- Clear indication of where the **live camera recognition** hooks into the UI.

> Build the UI against this contract. The backend/camera is a separate concern that
> produces `onSignDetected(sign)` events the UI consumes.
