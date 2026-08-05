const API_CONFIG = {
  inferEndpoint: "/api/infer",
  ordersEndpoint: "/api/orders",
  frameIntervalMs: 200,
  captureWindowMs: 2000
};

const menu = [
  { id: "burger", name: "House Burger", icon: "🍔", basePrice: 11.5, category: "Mains", description: "Beef patty, crisp lettuce, tomato" },
  { id: "pancakes", name: "Stack Pancakes", icon: "🥞", basePrice: 9.25, category: "Breakfast", description: "Three cakes with maple syrup" },
  { id: "pizza", name: "Margherita Pizza", icon: "🍕", basePrice: 12.75, category: "Mains", description: "Tomato, mozzarella, basil" },
  { id: "corn", name: "Corn Bowl", icon: "🌽", basePrice: 8.5, category: "Bowls", description: "Roasted corn, rice, greens" },
  { id: "toast", name: "Sweet Toast", icon: "🍞", basePrice: 7.75, category: "Breakfast", description: "Thick toast with seasonal fruit" },
  { id: "salad", name: "Market Salad", icon: "🥗", basePrice: 10.25, category: "Bowls", description: "Greens, vegetables, house dressing" },
  { id: "iced-tea", name: "Iced Tea", icon: "🧊", basePrice: 3.5, category: "Drinks", description: "Fresh brewed, lightly sweet" },
  { id: "lemonade", name: "Lemonade", icon: "🍋", basePrice: 4.0, category: "Drinks", description: "Bright lemon, served cold" },
  { id: "coffee", name: "Coffee", icon: "☕", basePrice: 3.25, category: "Drinks", description: "Hot drip coffee" }
];

const state = {
  screen: "select",
  category: "All",
  items: [],
  preferenceIndex: 0,
  toast: "",
  stream: null,
  cameraStatus: "idle",
  infer: {
    scanId: createId(),
    clipSeq: 1,
    frames: [],
    inFlight: false,
    status: "idle",
    displayText: "",
    accepted: false,
    paused: false,
    windowComplete: false,
    cooldownMs: 300,
    capturing: false,
    captureStartedAt: 0,
    error: ""
  },
  inferTimer: null,
  order: {
    status: "idle",
    error: "",
    orderId: "",
    idempotencyKey: createId()
  }
};

const app = document.querySelector("#app");

function money(value) {
  return new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" }).format(value);
}

function createId() {
  if (crypto.randomUUID) return crypto.randomUUID();
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = [...bytes].map(byte => byte.toString(16).padStart(2, "0"));
  return `${hex.slice(0, 4).join("")}-${hex.slice(4, 6).join("")}-${hex.slice(6, 8).join("")}-${hex.slice(8, 10).join("")}-${hex.slice(10, 16).join("")}`;
}

function escapeHtml(value) {
  return String(value).replace(/[&<>'"]/g, character => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    "'": "&#39;",
    '"': "&quot;"
  })[character]);
}

function itemCount() {
  return state.items.reduce((sum, item) => sum + item.qty, 0);
}

function subtotal() {
  return state.items.reduce((sum, item) => sum + item.dish.basePrice * item.qty, 0);
}

function currentItem() {
  return state.items[state.preferenceIndex];
}

function categories() {
  return ["All", ...new Set(menu.map(item => item.category))];
}

function stepper(active) {
  return `<div class="stepper" aria-label="Order steps">
    ${["Select", "Preferences", "Checkout"].map(step => `<span class="step ${active === step ? "active" : ""}">${step}</span>`).join("")}
  </div>`;
}

function topLine(active) {
  return `<div class="topline">
    <div class="brand"><span class="brand-mark">🍽️</span><span>Harbor Counter</span></div>
    ${stepper(active)}
  </div>`;
}

function render() {
  if (state.screen === "success") return renderSuccess();
  if (state.screen === "preferences") return renderPreferences();
  if (state.screen === "checkout") return renderCheckout();
  renderSelect();
}

function renderSelect() {
  stopCameraAndInference();
  const visibleMenu = state.category === "All" ? menu : menu.filter(item => item.category === state.category);
  app.innerHTML = `<section class="shell">
    ${topLine("Select")}
    <div class="hero">
      <div>
        <div class="eyebrow">Step 1 · menu</div>
        <h1>Choose your order.</h1>
        <p class="lead">Use plus and minus to adjust quantities. Review preferences before checkout.</p>
      </div>
      <div class="notice">Fast ordering for the counter. Large controls are ready for touchscreens.</div>
    </div>
    <div class="category-row" aria-label="Menu categories">
      ${categories().map(category => `<button class="category-btn ${state.category === category ? "active" : ""}" data-category="${category}">${category}</button>`).join("")}
    </div>
    <div class="dish-grid">
      ${visibleMenu.map(dishCard).join("")}
    </div>
    <div class="cart-bar">
      <div class="cart-total">🧾 ${itemCount()} item${itemCount() === 1 ? "" : "s"} · ${money(subtotal())}</div>
      <button class="btn primary" data-action="start-preferences" ${state.items.length ? "" : "disabled"}>Next → Preferences</button>
    </div>
  </section>${toastMarkup()}`;
  bindCommon();
  document.querySelectorAll("[data-category]").forEach(button => button.addEventListener("click", () => { state.category = button.dataset.category; render(); }));
  document.querySelectorAll("[data-add]").forEach(button => button.addEventListener("click", () => addDish(button.dataset.add)));
  document.querySelectorAll("[data-remove]").forEach(button => button.addEventListener("click", () => removeDish(button.dataset.remove)));
}

function dishCard(dish) {
  const selected = state.items.find(item => item.dish.id === dish.id);
  const qty = selected?.qty || 0;
  return `<article class="dish-card ${qty ? "selected" : ""}">
    <span class="food-icon">${dish.icon}</span>
    <span class="dish-name">${dish.name}</span>
    <p class="dish-description">${dish.description}</p>
    <span class="dish-meta"><span>${dish.category}</span><span>${money(dish.basePrice)}</span></span>
    <div class="qty-row" aria-label="Quantity for ${dish.name}">
      <button class="qty-btn" data-remove="${dish.id}" ${qty ? "" : "disabled"}>−</button>
      <span class="qty-count">${qty}</span>
      <button class="qty-btn" data-add="${dish.id}">+</button>
    </div>
  </article>`;
}

function addDish(id) {
  const existing = state.items.find(item => item.dish.id === id);
  if (existing) {
    existing.qty += 1;
  } else {
    const dish = menu.find(entry => entry.id === id);
    state.items.push({ dish, qty: 1, preferences: [] });
  }
  showToast("Quantity updated");
  render();
}

function removeDish(id) {
  const existingIndex = state.items.findIndex(item => item.dish.id === id);
  if (existingIndex < 0) return;
  state.items[existingIndex].qty -= 1;
  if (state.items[existingIndex].qty <= 0) state.items.splice(existingIndex, 1);
  showToast("Quantity updated");
  render();
}

function renderPreferences() {
  if (!state.items.length) { state.screen = "select"; return render(); }
  const item = currentItem();
  if (!item) { state.preferenceIndex = 0; return render(); }
  app.innerHTML = `<section class="shell">
    ${topLine("Preferences")}
    <div class="custom-layout">
      <div class="panel">
        <div class="dish-header"><span class="food-icon">${item.dish.icon}</span><div><div class="progress-line">Item ${state.preferenceIndex + 1} of ${state.items.length}</div><h2>${item.dish.name}</h2></div></div>
        <p class="lead">The camera starts automatically. Add a preference or continue without one.</p>
        <div class="preference-result ${state.infer.accepted ? "accepted" : ""}">
          <span class="result-kicker">Preference</span>
          <strong id="preferenceResultText">${escapeHtml(displayPreferenceText(item))}</strong>
        </div>
        <div id="preferenceListRegion">${preferenceList(item)}</div>
      </div>
      <div>
        <div class="camera-card">
          <div class="camera-window">
            <video id="camera" autoplay playsinline muted></video>
            <div id="cameraDot" class="camera-dot" aria-label="Capture window active"><span class="sr-only">Capture window active</span></div>
            <div class="camera-placeholder" id="cameraPlaceholder"><span>📷</span></div>
          </div>
        </div>
        <div class="panel service-panel">
          <div id="serviceMessage" class="boundary ${state.infer.status === "unavailable" ? "error" : "quiet"}">${escapeHtml(serviceMessage())}</div>
          <div class="action-row">
            <button class="btn primary" data-action="next-item">Next item</button>
            <button class="btn green" data-action="checkout">Checkout</button>
            <button class="btn" data-action="retry-inference">Retry camera</button>
            <button class="btn" data-action="back-select">← Menu</button>
          </div>
        </div>
      </div>
    </div>
  </section>${toastMarkup()}`;
  bindCommon();
  bindCameraElement();
  ensurePreferenceCamera();
}

function latestPreference(item) {
  return item.preferences.at(-1)?.displayText || "";
}

function displayPreferenceText(item) {
  return latestPreference(item) || state.infer.displayText || "";
}

function preferenceList(item) {
  const chips = item.preferences.length
    ? item.preferences.map(pref => `<span class="chip confirmed">${escapeHtml(pref.displayText)}</span>`).join("")
    : "";
  return `<h3>Saved for this item</h3><div class="chip-row">${chips}</div>`;
}

function serviceMessage() {
  if (state.infer.status === "unavailable") return state.infer.error || "Preferences are unavailable. You may retry or continue without one.";
  return "";
}

async function ensurePreferenceCamera() {
  if (state.stream) {
    startInferenceLoop();
    return;
  }
  try {
    state.stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "user" }, audio: false });
    state.cameraStatus = "live";
    state.infer.status = "listening";
    bindCameraElement();
    updatePreferenceRegions();
  } catch (error) {
    state.cameraStatus = "unavailable";
    state.infer.status = "unavailable";
    state.infer.error = "Camera permission is required to add preferences.";
    updatePreferenceRegions();
  }
}

function bindCameraElement() {
  const video = document.querySelector("#camera");
  const placeholder = document.querySelector("#cameraPlaceholder");
  if (video && state.stream) {
    video.srcObject = state.stream;
    video.classList.add("live");
    if (placeholder) placeholder.style.display = "none";
    startInferenceLoop();
  }
}

function startInferenceLoop() {
  if (state.inferTimer || !state.stream || state.infer.accepted || state.infer.paused || state.infer.status === "unavailable") return;
  state.infer.status = "listening";
  beginCaptureWindow(state.infer.scanId);
}

function stopInferenceLoop() {
  if (state.inferTimer) window.clearTimeout(state.inferTimer);
  state.inferTimer = null;
  state.infer.capturing = false;
  updateCameraDot();
}

function beginCaptureWindow(requestScanId) {
  if (state.infer.scanId !== requestScanId || state.infer.accepted || state.infer.paused || state.infer.status === "unavailable" || !state.stream) return;
  const video = document.querySelector("#camera");
  if (!video || video.readyState < 2) {
    state.inferTimer = window.setTimeout(() => beginCaptureWindow(requestScanId), API_CONFIG.frameIntervalMs);
    return;
  }
  state.infer.windowComplete = false;
  state.infer.capturing = true;
  state.infer.captureStartedAt = performance.now();
  state.infer.frames = [];
  updateCameraDot();
  captureClipFrame(requestScanId);
}

function captureClipFrame(requestScanId) {
  state.inferTimer = null;
  const item = currentItem();
  const video = document.querySelector("#camera");
  if (!item || !video || state.infer.scanId !== requestScanId || state.infer.accepted || state.infer.paused || state.infer.status === "unavailable") return;
  if (video.readyState >= 2) state.infer.frames.push(captureFrame(video));
  if (performance.now() - state.infer.captureStartedAt < API_CONFIG.captureWindowMs) {
    state.inferTimer = window.setTimeout(() => captureClipFrame(requestScanId), API_CONFIG.frameIntervalMs);
    return;
  }
  sendClipForInference(requestScanId);
}

async function sendClipForInference(requestScanId) {
  const item = currentItem();
  if (!item || state.infer.scanId !== requestScanId || state.infer.accepted || state.infer.paused || state.infer.inFlight) return;
  const clipSeq = state.infer.clipSeq;
  const frames = state.infer.frames.slice();
  state.infer.inFlight = true;
  state.infer.clipSeq += 1;
  state.infer.capturing = false;
  updateCameraDot();
  try {
    const response = await fetch(API_CONFIG.inferEndpoint, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        scan_id: requestScanId,
        clip_seq: clipSeq,
        item: { id: item.dish.id, quantity: item.qty },
        frames
      })
    });
    if (!response.ok) throw new Error(`Preference request failed ${response.status}`);
    const data = await response.json();
    if (data.scan_id !== requestScanId || state.infer.scanId !== requestScanId) return;
    consumeInferenceResponse(data);
  } catch (error) {
    if (state.infer.scanId !== requestScanId) return;
    stopInferenceLoop();
    state.infer.status = "unavailable";
    state.infer.error = "Preferences are unavailable. Please retry or continue without one.";
    updatePreferenceRegions();
  } finally {
    if (state.infer.scanId === requestScanId) state.infer.inFlight = false;
    if (state.infer.scanId === requestScanId && !state.infer.accepted && state.infer.status !== "unavailable") {
      state.inferTimer = window.setTimeout(() => beginCaptureWindow(requestScanId), state.infer.cooldownMs);
    }
  }
}

function captureFrame(video) {
  const canvas = document.createElement("canvas");
  canvas.width = 320;
  canvas.height = Math.round((video.videoHeight / video.videoWidth) * canvas.width) || 240;
  const context = canvas.getContext("2d");
  context.drawImage(video, 0, 0, canvas.width, canvas.height);
  return canvas.toDataURL("image/jpeg", 0.72);
}

function consumeInferenceResponse(data) {
  state.infer.windowComplete = Boolean(data.window_complete);
  state.infer.cooldownMs = Number.isFinite(data.cooldown_ms) ? data.cooldown_ms : 300;
  if (state.infer.windowComplete && typeof data.display_text === "string" && data.display_text.trim()) {
    state.infer.displayText = data.display_text;
  }
  state.infer.paused = Boolean(data.inference_paused);
  state.infer.accepted = Boolean(data.accepted) && state.infer.paused;
  state.infer.status = state.infer.paused ? "paused" : "listening";
  if (state.infer.windowComplete) {
    state.infer.capturing = false;
    updateCameraDot();
  }
  if (state.infer.paused) stopInferenceLoop();
  if (state.infer.accepted && state.infer.displayText) {
    const item = currentItem();
    if (!item.preferences.some(pref => pref.scanId === state.infer.scanId)) {
      item.preferences.push({ scanId: state.infer.scanId, displayText: state.infer.displayText, acceptedAt: new Date().toISOString() });
    }
    stopInferenceLoop();
  }
  updatePreferenceRegions();
}

function updatePreferenceRegions() {
  if (state.screen !== "preferences") return;
  const item = currentItem();
  if (!item) return;
  const result = document.querySelector("#preferenceResultText");
  const list = document.querySelector("#preferenceListRegion");
  const service = document.querySelector("#serviceMessage");
  const card = document.querySelector(".preference-result");
  const placeholder = document.querySelector("#cameraPlaceholder");
  if (result) result.textContent = displayPreferenceText(item);
  if (list) list.innerHTML = preferenceList(item);
  if (card) card.classList.toggle("accepted", Boolean(item.preferences.length));
  if (service) {
    service.textContent = serviceMessage();
    service.classList.toggle("error", state.infer.status === "unavailable");
    service.classList.toggle("quiet", state.infer.status !== "unavailable");
  }
  if (placeholder && state.cameraStatus === "unavailable") placeholder.innerHTML = "<span>📷</span>Camera unavailable";
  updateCameraDot();
}

function updateCameraDot() {
  const dot = document.querySelector("#cameraDot");
  if (!dot) return;
  dot.classList.toggle("active", Boolean(state.infer.capturing && !state.infer.accepted && !state.infer.paused && state.cameraStatus !== "unavailable"));
}

function resetInferenceState() {
  stopInferenceLoop();
  state.infer = { scanId: createId(), clipSeq: 1, frames: [], inFlight: false, status: "idle", displayText: "", accepted: false, paused: false, windowComplete: false, cooldownMs: 300, capturing: false, captureStartedAt: 0, error: "" };
}

function nextItemOrCheckout() {
  resetInferenceState();
  if (state.preferenceIndex < state.items.length - 1) {
    state.preferenceIndex += 1;
    render();
  } else {
    stopCameraAndInference();
    state.screen = "checkout";
    render();
  }
}

function renderCheckout() {
  stopCameraAndInference();
  const tax = subtotal() * 0.0825;
  const grand = subtotal() + tax;
  app.innerHTML = `<section class="shell">
    ${topLine("Checkout")}
    <div class="hero">
      <div><div class="eyebrow">Step 3 · review</div><h1>Review your order.</h1><p class="lead">Confirm the items and preferences below before sending to the counter.</p></div>
    </div>
    <div class="checkout-grid">
      <div>${state.items.map(summaryCard).join("")}</div>
      <div class="panel totals">
        <div class="total-line"><span>Subtotal</span><strong>${money(subtotal())}</strong></div>
        <div class="total-line"><span>Tax</span><strong>${money(tax)}</strong></div>
        <div class="total-line grand"><span>Total</span><strong>${money(grand)}</strong></div>
        ${orderStatusMarkup()}
        <div class="action-row">
          <button class="btn green" data-action="send" ${state.order.status === "sending" ? "disabled" : ""}>${state.order.status === "sending" ? "Sending…" : "Send Order"}</button>
          <button class="btn" data-action="back-preferences" ${state.order.status === "sending" ? "disabled" : ""}>← Edit preferences</button>
          <button class="btn red" data-action="reset" ${state.order.status === "sending" ? "disabled" : ""}>Reset</button>
        </div>
      </div>
    </div>
  </section>${toastMarkup()}`;
  bindCommon();
}

function summaryCard(item, index) {
  const prefs = item.preferences.length
    ? item.preferences.map(pref => `<span class="chip confirmed">${escapeHtml(pref.displayText)}</span>`).join("")
    : "";
  return `<article class="summary-card">
    <div class="summary-title"><span>${item.dish.icon} ${item.dish.name}</span><span>${money(item.dish.basePrice * item.qty)}</span></div>
    <p class="progress-line">Item ${index + 1} · quantity ${item.qty} · ${money(item.dish.basePrice)} each</p>
    <div class="chip-row">${prefs}</div>
  </article>`;
}

function orderStatusMarkup() {
  if (state.order.status === "sending") return `<div class="boundary">Sending order to the counter…</div>`;
  if (state.order.status === "error") return `<div class="boundary error">${state.order.error}</div>`;
  return "";
}

async function submitOrder() {
  const idempotencyKey = state.order.idempotencyKey;
  state.order = { status: "sending", error: "", orderId: "", idempotencyKey };
  render();
  try {
    const response = await fetch(API_CONFIG.ordersEndpoint, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", "Idempotency-Key": idempotencyKey },
      body: JSON.stringify(orderPayload())
    });
    if (!response.ok) throw new Error(`Order request failed ${response.status}`);
    const data = await response.json();
    if (data.persisted !== true || !data.order_id) throw new Error("Order was not confirmed.");
    state.order = { status: "success", error: "", orderId: data.order_id, idempotencyKey };
    state.screen = "success";
    render();
  } catch (error) {
    state.order = {
      status: "error",
      error: "Ordering is unavailable. Please retry when the counter system is connected.",
      orderId: "",
      idempotencyKey
    };
    render();
  }
}

function orderPayload() {
  return {
    items: state.items.map(item => ({
      id: item.dish.id,
      quantity: item.qty,
      preferences: item.preferences.map(pref => pref.displayText)
    }))
  };
}

function renderSuccess() {
  app.innerHTML = `<section class="success"><div class="success-card">
    <div class="success-icon">✅</div>
    <h1>Order received.</h1>
    <p class="lead">Confirmation ${escapeHtml(state.order.orderId)}. Thank you.</p>
    <button class="btn primary" data-action="reset">New Order</button>
  </div></section>`;
  bindCommon();
}

function bindCommon() {
  document.querySelectorAll("[data-action]").forEach(button => {
    button.addEventListener("click", () => {
      const action = button.dataset.action;
      if (action === "start-preferences") { state.screen = "preferences"; state.preferenceIndex = 0; resetInferenceState(); render(); }
      if (action === "next-item") nextItemOrCheckout();
      if (action === "retry-inference") { resetInferenceState(); state.cameraStatus = state.stream ? "live" : "idle"; updatePreferenceRegions(); ensurePreferenceCamera(); }
      if (action === "back-select") { stopCameraAndInference(); state.screen = "select"; render(); }
      if (action === "checkout") { stopCameraAndInference(); state.screen = "checkout"; render(); }
      if (action === "back-preferences") { state.order = { status: "idle", error: "", orderId: "", idempotencyKey: createId() }; state.screen = "preferences"; state.preferenceIndex = 0; resetInferenceState(); render(); }
      if (action === "send") submitOrder();
      if (action === "reset") resetOrder();
    });
  });
}

function stopCameraAndInference() {
  stopInferenceLoop();
  if (state.stream) state.stream.getTracks().forEach(track => track.stop());
  state.stream = null;
  state.cameraStatus = "idle";
}

function resetOrder() {
  stopCameraAndInference();
  state.screen = "select";
  state.category = "All";
  state.items = [];
  state.preferenceIndex = 0;
  resetInferenceState();
  state.order = { status: "idle", error: "", orderId: "", idempotencyKey: createId() };
  showToast("New order ready");
  render();
}

function showToast(message) {
  state.toast = message;
  window.clearTimeout(showToast.timer);
  showToast.timer = window.setTimeout(() => { state.toast = ""; render(); }, 1500);
}

function toastMarkup() {
  return state.toast ? `<div class="toast" role="status">${state.toast}</div>` : "";
}

render();
