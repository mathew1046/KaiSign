const API_CONFIG = {
  inferEndpoint: "/api/infer",
  ordersEndpoint: "/api/orders",
  liveEndpoint: "/ws/live",
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

const CATEGORY_IDS = {
  all: "All",
  mains: "Mains",
  breakfast: "Breakfast",
  bowls: "Bowls",
  drinks: "Drinks"
};

const NOTE_LIMIT = 10;
const NOTE_CHAR_LIMIT = 160;
const MAX_QUANTITY = 20;
const PLAYBACK_QUEUE_SECONDS_LIMIT = 30;
const CAMERA_CONSTRAINTS = {
  video: {
    facingMode: { ideal: "user" },
    width: { ideal: 320, max: 320 },
    height: { ideal: 240, max: 240 },
    frameRate: { ideal: 8, max: 8 }
  },
  audio: false
};
const CAMERA_CONSTRAINT_ATTEMPTS = [
  CAMERA_CONSTRAINTS,
  { video: { facingMode: { ideal: "user" }, width: { max: 320 }, height: { max: 240 }, frameRate: { max: 8 } }, audio: false },
  { video: { width: { max: 320 }, height: { max: 240 }, frameRate: { max: 8 } }, audio: false }
];

const state = {
  mode: "choice",
  screen: "mode",
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
  },
  keypad: {
    focusId: "mode-normal",
    announcement: "Use the numpad arrows to move and 5 to select."
  }
};

const app = document.querySelector("#app");
let liveClient;
let wakeClient;
let playback;
let voice;
let captureCanvas;
let captureContext;
let inferAbortController;
let releasedInferenceScanId = "";

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

function liveUrl() {
  const protocol = location.protocol === "https:" ? "wss:" : "ws:";
  return `${protocol}//${location.host}${API_CONFIG.liveEndpoint}`;
}

function decodeBase64Pcm(value) {
  const binary = atob(value || "");
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) bytes[index] = binary.charCodeAt(index);
  return new Int16Array(bytes.buffer);
}

class LivePlayback {
  constructor() {
    this.context = null;
    this.nextTime = 0;
    this.sources = new Set();
  }

  async play(pcm16Base64, sampleRate = 24000) {
    const pcm = decodeBase64Pcm(pcm16Base64);
    if (!pcm.length) return;
    if (!this.context) this.context = new AudioContext();
    if (this.context.state === "suspended") await this.context.resume();
    const buffer = this.context.createBuffer(1, pcm.length, sampleRate || 24000);
    const data = buffer.getChannelData(0);
    for (let index = 0; index < pcm.length; index += 1) data[index] = Math.max(-1, Math.min(1, pcm[index] / 32768));
    const queuedUntil = Math.max(this.nextTime || 0, this.context.currentTime);
    if (queuedUntil - this.context.currentTime + buffer.duration > PLAYBACK_QUEUE_SECONDS_LIMIT) this.clear();
    const source = this.context.createBufferSource();
    source.buffer = buffer;
    source.connect(this.context.destination);
    source.addEventListener("ended", () => this.sources.delete(source));
    this.sources.add(source);
    const startAt = Math.max(this.context.currentTime + 0.02, this.nextTime || 0);
    source.start(startAt);
    this.nextTime = startAt + buffer.duration;
  }

  clear() {
    this.sources.forEach(source => {
      try { source.stop(); } catch { /* already stopped */ }
    });
    this.sources.clear();
    this.nextTime = this.context ? this.context.currentTime : 0;
  }
}

playback = new LivePlayback();

class LiveClient {
  constructor(owner, mode, context = {}, epoch, options = {}) {
    this.owner = owner;
    this.mode = mode;
    this.context = context;
    this.epoch = epoch;
    this.sessionId = createId();
    this.socket = null;
    this.openTimer = null;
    this.retryTimer = null;
    this.pending = [];
    this.ready = false;
    this.retryPreReady = Boolean(options.retryPreReady);
    this.retryCount = 0;
    this.retryUntil = 0;
    this.serverError = false;
    this.deliberateClose = false;
  }

  start() {
    this.openTimer = window.setTimeout(() => {
      if (this.owner.isClientCurrent(this)) this.open();
    }, 80);
  }

  open() {
    if (!this.owner.isClientCurrent(this)) return;
    if (this.retryPreReady && !this.retryUntil) this.retryUntil = Date.now() + 5000;
    this.socket = new WebSocket(liveUrl());
    this.socket.addEventListener("open", () => {
      if (!this.owner.isClientCurrent(this)) return this.close(false);
      const start = { type: "start", mode: this.mode, session_id: this.sessionId };
      if (this.context && Object.keys(this.context).length) start.context = this.context;
      this.send(start);
    });
    this.socket.addEventListener("message", event => this.receive(event));
    this.socket.addEventListener("close", event => {
      if (!this.owner.isClientCurrent(this)) return;
      const retryableCode = [1006, 1013].includes(event.code);
      const canRetry = this.retryPreReady && !this.deliberateClose && !this.serverError && !this.ready && retryableCode && Date.now() < this.retryUntil;
      this.ready = false;
      if (canRetry) {
        this.retryCount += 1;
        const delay = Math.min(1000, 150 * (2 ** Math.min(this.retryCount - 1, 4)));
        this.retryTimer = window.setTimeout(() => {
          this.retryTimer = null;
          if (this.owner.isClientCurrent(this) && !this.ready && !this.serverError && !this.deliberateClose) this.open();
        }, delay);
      }
    });
    this.socket.addEventListener("error", () => { if (this.owner.isClientCurrent(this)) this.ready = false; });
  }

  send(message) {
    if (!this.owner.isClientCurrent(this)) return;
    if (!this.socket || this.socket.readyState !== WebSocket.OPEN) return;
    this.socket.send(JSON.stringify(message));
  }

  receive(event) {
    if (!this.owner.isClientCurrent(this)) return;
    let message;
    try { message = JSON.parse(event.data); } catch { return; }
    if (message.type === "ready") {
      this.ready = true;
      this.pending.splice(0).forEach(pending => { if (this.owner.isClientCurrent(this)) this.send(pending); });
    }
    if (message.type === "audio" && message.pcm16_base64 && this.mode === "blind" && state.mode === "blind") playback.play(message.pcm16_base64, message.sample_rate).catch(() => {});
    if (message.type === "interrupted") playback.clear();
    if (message.type === "wake_detected") activateBlindMode(this);
    if (message.type === "state") applyLiveState(message, this);
    if (message.type === "error") {
      this.serverError = true;
      this.ready = false;
      if (this.retryTimer) window.clearTimeout(this.retryTimer);
      this.retryTimer = null;
    }
  }

  close(sendStop = true) {
    if (this.openTimer) window.clearTimeout(this.openTimer);
    if (this.retryTimer) window.clearTimeout(this.retryTimer);
    this.deliberateClose = true;
    this.pending = [];
    this.ready = false;
    if (this.socket && this.socket.readyState === WebSocket.OPEN && sendStop && this.owner.isClientCurrent(this)) this.send({ type: "stop" });
    if (this.socket && [WebSocket.OPEN, WebSocket.CONNECTING].includes(this.socket.readyState)) this.socket.close();
    this.socket = null;
  }
}

class VoiceController {
  constructor() {
    this.epoch = 0;
    this.kind = "none";
    this.client = null;
    this.transitioning = false;
  }

  isClientCurrent(client) {
    return this.client === client && client.epoch === this.epoch;
  }

  async start(kind, context = {}, options = {}) {
    const previousClient = this.client;
    if (previousClient) previousClient.close(true);
    this.epoch += 1;
    const epoch = this.epoch;
    this.kind = kind;
    this.client = null;
    const client = new LiveClient(this, kind, context, epoch, options);
    this.client = client;
    liveClient = kind === "wake" ? null : client;
    wakeClient = kind === "wake" ? client : null;
    client.start();
  }

  stop() {
    const client = this.client;
    if (client) client.close(true);
    this.epoch += 1;
    this.kind = "none";
    this.client = null;
    liveClient = null;
    wakeClient = null;
  }

  async activateBlindFromWake(sourceClient) {
    if (this.transitioning || state.mode !== "choice" || this.kind !== "wake" || !this.isClientCurrent(sourceClient)) return;
    this.transitioning = true;
    sourceClient.close(true);
    this.epoch += 1;
    this.client = null;
    this.kind = "none";
    wakeClient = null;
    resetKeypadSelection();
    state.mode = "blind";
    if (state.screen === "mode") state.screen = "select";
    render();
    await this.start("blind", orderContext(), { retryPreReady: true });
    this.transitioning = false;
  }
}

voice = new VoiceController();

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

function setAnnouncement(message) {
  state.keypad.announcement = message;
  const region = document.querySelector("#keypadLiveRegion");
  if (region) region.textContent = message;
}

function keypadLegend() {
  return `<div class="keypad-legend" aria-hidden="true"><span>8 ↑</span><span>4 ←</span><strong>5 Select</strong><span>6 →</span><span>2 ↓</span><span>0 Back</span></div><div id="keypadLiveRegion" class="sr-only" aria-live="polite" aria-atomic="true">${escapeHtml(state.keypad.announcement)}</div>`;
}

function visibleSelectMenu() {
  return state.category === "All" ? menu : menu.filter(item => item.category === state.category);
}

function resetKeypadSelection() {
  state.keypad.focusId = "mode-normal";
}

function focusClass(id) {
  return state.keypad.focusId === id ? "key-focus" : "";
}

function focusAttrs(id) {
  return `${focusClass(id)}" data-focus-id="${id}" ${state.keypad.focusId === id ? 'aria-current="true"' : ""}`;
}

function selectFocusables() {
  const visibleMenu = visibleSelectMenu();
  const entries = categories().map(category => ({ id: `cat-${category}`, label: category, action: () => selectCategory(category) }));
  visibleMenu.forEach(dish => {
    const qty = state.items.find(item => item.dish.id === dish.id)?.qty || 0;
    if (qty > 0) entries.push({ id: `remove-${dish.id}`, label: `Remove ${dish.name}`, action: () => removeFocusedDish(dish) });
    entries.push({ id: `add-${dish.id}`, label: `Add ${dish.name}`, action: () => addFocusedDish(dish) });
  });
  if (state.items.length) entries.push({ id: "select-next", label: "Preferences", action: startPreferencesFlow });
  return entries;
}

function preferenceFocusables() {
  const deafMode = state.mode === "deaf";
  return [
    { id: "pref-prev", label: "Previous item", action: previousPreferenceItem },
    { id: "pref-next", label: "Next item", action: nextItemOrCheckout },
    { id: "pref-checkout", label: "Checkout", action: goCheckout },
    ...(deafMode ? [{ id: "pref-retry", label: "Retry camera", action: retryInference }] : []),
    { id: "pref-back", label: "Menu", action: backToSelect }
  ];
}

function checkoutFocusables() {
  if (state.order.status === "sending") return [];
  return [
    { id: "checkout-send", label: "Send order", action: submitOrder },
    { id: "checkout-edit", label: "Edit preferences", action: editPreferencesFromCheckout },
    { id: "checkout-reset", label: "Reset order", action: resetOrder }
  ];
}

function currentFocusables() {
  if (state.screen === "mode") return [
    { id: "mode-normal", label: "Normal", action: () => startMode("normal") },
    { id: "mode-deaf", label: "Deaf", action: () => startMode("deaf") }
  ];
  if (state.screen === "select") return selectFocusables();
  if (state.screen === "preferences") return preferenceFocusables();
  if (state.screen === "checkout") return checkoutFocusables();
  if (state.screen === "success") return [{ id: "success-new", label: "New order", action: resetOrder }];
  return [];
}

function ensureFocus() {
  const focusables = currentFocusables();
  if (!focusables.some(entry => entry.id === state.keypad.focusId)) state.keypad.focusId = focusables[0]?.id || "";
}

function focusedEntry() {
  ensureFocus();
  return currentFocusables().find(entry => entry.id === state.keypad.focusId);
}

function scrollFocusedIntoView() {
  requestAnimationFrame(() => {
    document.querySelector(`[data-focus-id="${CSS.escape(state.keypad.focusId)}"]`)?.scrollIntoView({ block: "center", inline: "nearest", behavior: "smooth" });
  });
}

function availableFocusableEntries() {
  return currentFocusables().map(entry => {
    const element = document.querySelector(`[data-focus-id="${CSS.escape(entry.id)}"]`);
    return element && !element.disabled && element.getClientRects().length ? { ...entry, element, rect: element.getBoundingClientRect() } : null;
  }).filter(Boolean);
}

function rectCenter(rect) {
  return { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 };
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
  if (state.screen === "mode") renderModeChoice();
  else if (state.screen === "success") renderSuccess();
  else if (state.screen === "preferences") renderPreferences();
  else if (state.screen === "checkout") renderCheckout();
  else renderSelect();
  scrollFocusedIntoView();
}

function renderModeChoice() {
  stopCameraAndInference();
  ensureFocus();
  app.innerHTML = `<section class="mode-screen">
    ${keypadLegend()}
    <div class="mode-card">
      <button class="mode-choice normal ${focusAttrs("mode-normal")}>Normal</button>
      <button class="mode-choice deaf ${focusAttrs("mode-deaf")}>Deaf</button>
    </div>
  </section>`;
}

function startMode(mode) {
  resetKeypadSelection();
  state.mode = mode;
  state.screen = "select";
  stopLiveSession();
  if (mode !== "choice") stopWakeListener();
  render();
}

function renderSelect() {
  if (state.mode !== "deaf") stopCameraAndInference();
  const visibleMenu = visibleSelectMenu();
  ensureFocus();
  app.innerHTML = `<section class="shell ${state.mode === "blind" ? "voice-driven" : ""}">
    ${topLine("Select")}
    ${keypadLegend()}
    <div class="hero">
      <div>
        <div class="eyebrow">Menu</div>
        <h1>Choose your order.</h1>
        <p class="lead">Build your cart, then add preferences.</p>
      </div>
    </div>
    <div class="category-row" aria-label="Menu categories">
      ${categories().map(category => `<button class="category-btn ${state.category === category ? "active" : ""} ${focusAttrs(`cat-${category}`)} data-category="${category}">${category}</button>`).join("")}
    </div>
    <div class="dish-grid">
      ${visibleMenu.map((dish, index) => dishCard(dish, index)).join("")}
    </div>
    <div class="cart-bar">
      <div class="cart-total">🧾 ${itemCount()} item${itemCount() === 1 ? "" : "s"} · ${money(subtotal())}</div>
      <button class="btn primary ${focusAttrs("select-next")} data-action="start-preferences" ${state.items.length ? "" : "disabled"}>Next → Preferences</button>
    </div>
  </section>${toastMarkup()}`;
}

function dishCard(dish, index = 0) {
  const selected = state.items.find(item => item.dish.id === dish.id);
  const qty = selected?.qty || 0;
  const focused = state.keypad.focusId === `add-${dish.id}` || state.keypad.focusId === `remove-${dish.id}`;
  return `<article class="dish-card ${qty ? "selected" : ""} ${focused ? "focused" : ""}">
    <span class="food-icon">${dish.icon}</span>
    <span class="dish-name">${dish.name}</span>
    <p class="dish-description">${dish.description}</p>
    <span class="dish-meta"><span>${dish.category}</span><span>${money(dish.basePrice)}</span></span>
    <div class="qty-row" aria-label="Quantity for ${dish.name}">
      <button class="qty-btn ${focusAttrs(`remove-${dish.id}`)} data-remove="${dish.id}" ${qty ? "" : "disabled"}>−</button>
      <span class="qty-count">${qty}</span>
      <button class="qty-btn ${focusAttrs(`add-${dish.id}`)} data-add="${dish.id}">+</button>
    </div>
  </article>`;
}

function addDish(id, silent = false) {
  const dish = menu.find(entry => entry.id === id);
  if (!dish) return;
  const existing = state.items.find(item => item.dish.id === id);
  if (existing) {
    existing.qty = Math.min(MAX_QUANTITY, existing.qty + 1);
  } else {
    state.items.push({ dish, qty: 1, preferences: [] });
  }
  if (!silent) { showToast("Quantity updated"); render(); }
}

function selectCategory(category) {
  state.category = category;
  state.keypad.focusId = `cat-${category}`;
  render();
  scrollFocusedIntoView();
  setAnnouncement(`${category} selected.`);
}

function addFocusedDish(dish) {
  addDish(dish.id, true);
  state.keypad.focusId = `add-${dish.id}`;
  render();
  scrollFocusedIntoView();
  setAnnouncement(`${dish.name} added.`);
}

function removeFocusedDish(dish) {
  const existing = state.items.find(item => item.dish.id === dish.id);
  if (!existing) return setAnnouncement(`${dish.name} is not in your cart.`);
  removeDish(dish.id, true);
  const remaining = state.items.find(item => item.dish.id === dish.id)?.qty || 0;
  state.keypad.focusId = remaining > 0 ? `remove-${dish.id}` : `add-${dish.id}`;
  render();
  scrollFocusedIntoView();
  setAnnouncement(`${dish.name} removed.`);
}

function removeDish(id, silent = false) {
  const existingIndex = state.items.findIndex(item => item.dish.id === id);
  if (existingIndex < 0) return;
  state.items[existingIndex].qty -= 1;
  if (state.items[existingIndex].qty <= 0) state.items.splice(existingIndex, 1);
  if (!silent) { showToast("Quantity updated"); render(); }
}

function renderPreferences() {
  if (!state.items.length) { state.screen = "select"; return render(); }
  const item = currentItem();
  if (!item) { state.preferenceIndex = 0; return render(); }
  const deafMode = state.mode === "deaf";
  ensureFocus();
  app.innerHTML = `<section class="shell ${state.mode === "blind" ? "voice-driven" : ""}">
    ${topLine("Preferences")}
    ${keypadLegend()}
    <div class="custom-layout">
      <div class="panel">
        <div class="dish-header"><span class="food-icon">${item.dish.icon}</span><div><div class="progress-line">Item ${state.preferenceIndex + 1} of ${state.items.length}</div><h2>${item.dish.name}</h2></div></div>
        ${state.mode === "blind" ? "" : `<p class="lead">${deafMode ? "Add a preference with a gesture, or continue." : "Say a preference, or continue."}</p>`}
        <div class="preference-result ${state.infer.accepted || item.preferences.length ? "accepted" : ""}">
          <span class="result-kicker">Preference</span>
          <strong id="preferenceResultText">${escapeHtml(displayPreferenceText(item))}</strong>
        </div>
        <div id="preferenceListRegion">${preferenceList(item)}</div>
      </div>
      <div>
        ${deafMode ? `<div class="camera-card">
          <div class="camera-window">
            <video id="camera" autoplay playsinline muted></video>
            <div id="cameraDot" class="camera-dot" aria-hidden="true"></div>
            <div class="camera-placeholder" id="cameraPlaceholder"><span>📷</span></div>
          </div>
        </div>` : `<div class="panel voice-panel"><div class="voice-orb" aria-hidden="true"></div><h3>${escapeHtml(item.dish.name)}</h3><div class="chip-row">${item.preferences.map(pref => `<span class="chip confirmed">${escapeHtml(pref.displayText)}</span>`).join("")}</div></div>`}
        <div class="panel service-panel">
          <div id="serviceMessage" class="boundary ${state.infer.status === "unavailable" ? "error" : "quiet"}">${escapeHtml(serviceMessage())}</div>
          <div class="action-row">
            <button class="btn ${focusAttrs("pref-prev")} data-action="previous-item">Previous item</button>
            <button class="btn primary ${focusAttrs("pref-next")} data-action="next-item">Next item</button>
            <button class="btn green ${focusAttrs("pref-checkout")} data-action="checkout">Checkout</button>
            ${deafMode ? `<button class="btn ${focusAttrs("pref-retry")} data-action="retry-inference">Retry camera</button>` : ""}
            <button class="btn ${focusAttrs("pref-back")} data-action="back-select">← Menu</button>
          </div>
        </div>
      </div>
    </div>
  </section>${toastMarkup()}`;
  if (state.mode === "blind") return;
  if (deafMode) {
    bindCameraElement();
    ensurePreferenceCamera();
  }
}

function latestPreference(item) {
  return item.preferences.at(-1)?.displayText || "";
}

function displayPreferenceText(item) {
  return latestPreference(item) || (state.mode === "deaf" ? state.infer.displayText : "");
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
    state.stream = await openPreferenceCameraStream();
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

async function openPreferenceCameraStream() {
  let lastError;
  for (const constraints of CAMERA_CONSTRAINT_ATTEMPTS) {
    try {
      return await navigator.mediaDevices.getUserMedia(constraints);
    } catch (error) {
      lastError = error;
    }
  }
  throw lastError;
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
  const inferState = state.infer;
  const clipSeq = state.infer.clipSeq;
  const frames = state.infer.frames;
  const body = JSON.stringify({
    scan_id: requestScanId,
    clip_seq: clipSeq,
    item: { id: item.dish.id, quantity: item.qty },
    frames
  });
  if (state.infer.frames === frames) state.infer.frames = [];
  state.infer.inFlight = true;
  state.infer.clipSeq += 1;
  state.infer.capturing = false;
  const controller = new AbortController();
  inferAbortController = controller;
  updateCameraDot();
  try {
    const response = await fetch(API_CONFIG.inferEndpoint, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      signal: controller.signal,
      body
    });
    if (!response.ok) throw new Error(`Preference request failed ${response.status}`);
    const data = await response.json();
    if (data.scan_id !== requestScanId || state.infer !== inferState || state.infer.scanId !== requestScanId) return;
    consumeInferenceResponse(data);
  } catch (error) {
    if (controller.signal.aborted || error.name === "AbortError") return;
    if (state.infer !== inferState || state.infer.scanId !== requestScanId) return;
    stopInferenceLoop();
    state.infer.status = "unavailable";
    state.infer.error = "Preferences are unavailable. Please retry or continue without one.";
    updatePreferenceRegions();
  } finally {
    if (inferAbortController === controller) inferAbortController = null;
    if (controller.signal.aborted || state.infer !== inferState || state.infer.scanId !== requestScanId) return;
    state.infer.inFlight = false;
    if (!state.infer.accepted && state.infer.status !== "unavailable") {
      state.inferTimer = window.setTimeout(() => beginCaptureWindow(requestScanId), state.infer.cooldownMs);
    }
  }
}

function captureFrame(video) {
  if (!captureCanvas) captureCanvas = document.createElement("canvas");
  if (!captureContext) captureContext = captureCanvas.getContext("2d");
  captureCanvas.width = 320;
  captureCanvas.height = Math.round((video.videoHeight / video.videoWidth) * captureCanvas.width) || 240;
  captureContext.drawImage(video, 0, 0, captureCanvas.width, captureCanvas.height);
  return captureCanvas.toDataURL("image/jpeg", 0.72);
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
  if (state.infer.paused) releaseInferenceSession();
  if (state.infer.accepted && state.infer.displayText) {
    const item = currentItem();
    addPreferenceNote(item, state.infer.displayText);
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

function orderContext() {
  return { active_item_id: currentItem()?.dish.id, order: orderPayload().items };
}

function stopLiveSession() {
  voice.stop();
}

function stopWakeListener() {
  if (voice.kind === "wake") voice.stop();
}

async function ensureWakeListener() {
  if (voice.kind === "wake" || state.mode !== "choice") return;
  await voice.start("wake", {});
}

async function startNormalPreferenceSession() {
  stopCameraAndInference();
  if (voice.kind === "normal" && voice.client?.context.active_item_id === currentItem()?.dish.id) return;
  await voice.start("normal", orderContext());
}

async function activateBlindMode(sourceClient) {
  if (sourceClient) return voice.activateBlindFromWake(sourceClient);
  stopCameraAndInference();
  resetKeypadSelection();
  state.mode = "blind";
  if (state.screen === "mode") state.screen = "select";
  render();
  await voice.start("blind", orderContext());
}

function applyLiveState(message, client) {
  const envelope = message?.action;
  if (!isValidVoiceAction(envelope, client)) return;
  const action = envelope.action;
  if (state.mode === "normal" && !["add_note", "remove_note", "finish_customization", "end_session"].includes(action)) return;
  if (state.mode === "normal") applyNormalPreferenceAction(envelope);
  if (state.mode === "blind") applyBlindAction(envelope);
}

function isValidVoiceAction(envelope, client) {
  if (!envelope || envelope.schema_version !== "voice-action.v1") return false;
  if (envelope.session_id !== client?.sessionId) return false;
  if (envelope.mode !== client?.mode || envelope.mode !== state.mode) return false;
  return ["select_category", "add_item", "set_quantity", "remove_item", "add_note", "remove_note", "finish_customization", "continue_ordering", "review_order", "confirm_order", "end_session"].includes(envelope.action);
}

function cleanNote(value) {
  const note = String(value || "").trim().replace(/\s+/g, " ");
  if (!note) return "";
  return note.slice(0, NOTE_CHAR_LIMIT);
}

function actionItemId(payload = {}) {
  return payload.item_id || payload.id || payload.active_item_id || payload.menu_item_id;
}

function actionNote(payload = {}) {
  return cleanNote(payload.note || payload.text || payload.value || payload.preference);
}

function actionQuantity(payload = {}, fallback = 1) {
  const quantity = Number(payload.quantity ?? payload.qty ?? fallback);
  if (!Number.isFinite(quantity)) return fallback;
  return Math.max(0, Math.min(MAX_QUANTITY, Math.floor(quantity)));
}

function categoryFromId(value) {
  const key = String(value || "").trim().toLowerCase();
  if (CATEGORY_IDS[key]) return CATEGORY_IDS[key];
  const display = categories().find(category => category.toLowerCase() === key);
  return display || "";
}

function applyNormalPreferenceAction(envelope) {
  if (envelope.action === "end_session") return endVoiceSession();
  if (envelope.action === "finish_customization") return nextItemOrCheckout();
  const item = currentItem();
  if (!item) return;
  const note = actionNote(envelope.payload);
  if (envelope.action === "add_note") addPreferenceNote(item, note);
  if (envelope.action === "remove_note") removePreferenceNote(item, note);
  updatePreferenceRegions();
}

function applyBlindAction(envelope) {
  const payload = envelope.payload || {};
  const action = envelope.action;
  const hasSnapshotItems = Array.isArray(envelope.state?.items);
  const hasSnapshotCategory = typeof envelope.state?.category === "string";
  applyBackendSnapshot(envelope.state);
  if (action === "select_category" && !hasSnapshotCategory) {
    const category = categoryFromId(payload.category_id || payload.category);
    if (category) state.category = category;
  }
  if (action === "add_item" && !hasSnapshotItems) addDishSilently(actionItemId(payload), actionQuantity(payload, 1));
  if (action === "set_quantity" && !hasSnapshotItems) setDishQuantity(actionItemId(payload), actionQuantity(payload, 0));
  if (action === "remove_item" && !hasSnapshotItems) removeDishSilently(actionItemId(payload), actionQuantity(payload, 1));
  if (action === "add_note" && !hasSnapshotItems) addPreferenceNote(targetItem(payload, envelope.state), actionNote(payload));
  if (action === "remove_note" && !hasSnapshotItems) removePreferenceNote(targetItem(payload, envelope.state), actionNote(payload));
  if (["finish_customization", "continue_ordering"].includes(action)) state.screen = "select";
  if (action === "review_order") state.screen = "checkout";
  if (action === "confirm_order") return confirmOrderFromVoice();
  if (action === "end_session") return endVoiceSession();
  render();
}

function targetItem(payload = {}, snapshot = {}) {
  const id = actionItemId(payload) || snapshot.active_item_id;
  return state.items.find(item => item.dish.id === id) || currentItem();
}

function applyBackendSnapshot(snapshot) {
  if (!snapshot || typeof snapshot !== "object") return;
  const category = categoryFromId(snapshot.category);
  if (category) state.category = category;
  if (Array.isArray(snapshot.items)) state.items = normalizeSnapshotItems(snapshot.items);
  const screen = mapScreen(snapshot.screen);
  if (screen) state.screen = screen;
  if (snapshot.active_item_id) {
    const index = state.items.findIndex(item => item.dish.id === snapshot.active_item_id);
    if (index >= 0) state.preferenceIndex = index;
  }
  if (state.preferenceIndex >= state.items.length) state.preferenceIndex = Math.max(0, state.items.length - 1);
}

function snapshotItem(entry) {
  const dish = menu.find(item => item.id === (entry.item_id || entry.id));
  if (!dish) return null;
  const qty = actionQuantity(entry, 1);
  if (qty <= 0) return null;
  const rawNotes = Array.isArray(entry.preferences) ? entry.preferences : Array.isArray(entry.notes) ? entry.notes : [];
  return { dish, qty, preferences: normalizeNotes(rawNotes) };
}

function normalizeSnapshotItems(entries) {
  const byId = new Map();
  entries.map(snapshotItem).filter(Boolean).forEach(item => {
    const existing = byId.get(item.dish.id);
    if (!existing) {
      byId.set(item.dish.id, item);
      return;
    }
    existing.qty = Math.min(MAX_QUANTITY, existing.qty + item.qty);
    existing.preferences = normalizeNotes([...existing.preferences.map(pref => pref.displayText), ...item.preferences.map(pref => pref.displayText)]);
  });
  return [...byId.values()];
}

function normalizeNotes(notes) {
  const seen = new Set();
  return notes.map(note => cleanNote(typeof note === "string" ? note : note?.displayText || note?.note)).filter(note => {
    const key = note.toLowerCase();
    if (!note || seen.has(key)) return false;
    seen.add(key);
    return true;
  }).slice(0, NOTE_LIMIT).map(note => ({ scanId: createId(), displayText: note, acceptedAt: new Date().toISOString() }));
}

function mapScreen(value) {
  const screen = String(value || "").toLowerCase();
  if (["menu", "select", "category", "cart"].includes(screen)) return "select";
  if (["preferences", "preference", "notes"].includes(screen)) return "preferences";
  if (["checkout", "review", "review_order"].includes(screen)) return "checkout";
  return "";
}

function endVoiceSession() {
  playback.clear();
  stopLiveSession();
  resetKeypadSelection();
  state.mode = "choice";
  state.screen = "mode";
  render();
  ensureWakeListener();
}

function confirmOrderFromVoice() {
  state.screen = "checkout";
  render();
  if (!canSubmitOrder()) return;
  playback.clear();
  stopLiveSession();
  submitOrder();
}

function canSubmitOrder() {
  return state.items.length > 0 && !["sending", "success"].includes(state.order.status);
}

function validPreferenceIndex(index) {
  const next = Number(index);
  return Number.isInteger(next) && next >= 0 && next < state.items.length ? next : 0;
}

function addPreferenceNote(item, note) {
  note = cleanNote(note);
  if (!item || !note || item.preferences.length >= NOTE_LIMIT || item.preferences.some(pref => pref.displayText.toLowerCase() === note.toLowerCase())) return;
  item.preferences.push({ scanId: createId(), displayText: note, acceptedAt: new Date().toISOString() });
}

function removePreferenceNote(item, note) {
  note = cleanNote(note);
  if (!item || !note) return;
  item.preferences = item.preferences.filter(pref => pref.displayText.toLowerCase() !== note.toLowerCase());
}

function addDishSilently(id, qty = 1) {
  for (let count = 0; count < Math.max(1, Math.min(MAX_QUANTITY, qty)); count += 1) addDish(id, true);
}

function removeDishSilently(id, qty = 1) {
  for (let count = 0; count < Math.max(1, Math.min(MAX_QUANTITY, qty)); count += 1) removeDish(id, true);
}

function setDishQuantity(id, qty) {
  if (!menu.some(dish => dish.id === id) || !Number.isInteger(qty) || qty < 0 || qty > MAX_QUANTITY) return;
  const index = state.items.findIndex(item => item.dish.id === id);
  if (qty === 0 && index >= 0) state.items.splice(index, 1);
  if (qty > 0 && index >= 0) state.items[index].qty = qty;
  if (qty > 0 && index < 0) state.items.push({ dish: menu.find(entry => entry.id === id), qty, preferences: [] });
}

function resetInferenceState() {
  stopInferenceLoop();
  state.infer = { scanId: createId(), clipSeq: 1, frames: [], inFlight: false, status: "idle", displayText: "", accepted: false, paused: false, windowComplete: false, cooldownMs: 300, capturing: false, captureStartedAt: 0, error: "" };
}

function nextItemOrCheckout() {
  if (state.mode === "normal") stopLiveSession();
  resetInferenceState();
  if (state.preferenceIndex < state.items.length - 1) {
    state.preferenceIndex += 1;
    render();
    if (state.mode === "normal") startNormalPreferenceSession();
  } else {
    stopCameraAndInference();
    state.screen = "checkout";
    state.keypad.focusId = "checkout-send";
    render();
  }
}

function previousPreferenceItem() {
  if (!state.items.length) return;
  if (state.mode === "normal") stopLiveSession();
  resetInferenceState();
  state.preferenceIndex = Math.max(0, state.preferenceIndex - 1);
  render();
  if (state.mode === "normal") startNormalPreferenceSession();
}

function startPreferencesFlow() {
  if (!state.items.length) return setAnnouncement("Add at least one item before preferences.");
  state.screen = "preferences";
  state.keypad.focusId = "pref-next";
  state.preferenceIndex = 0;
  resetInferenceState();
  render();
  if (state.mode === "normal") startNormalPreferenceSession();
}

function backToSelect() {
  if (state.mode === "normal") stopLiveSession();
  stopCameraAndInference();
  state.screen = "select";
  state.keypad.focusId = "select-next";
  render();
}

function goCheckout() {
  if (state.mode === "normal") stopLiveSession();
  stopCameraAndInference();
  state.screen = "checkout";
  state.keypad.focusId = "checkout-send";
  render();
}

function retryInference() {
  resetInferenceState();
  state.cameraStatus = state.stream ? "live" : "idle";
  updatePreferenceRegions();
  ensurePreferenceCamera();
}

function editPreferencesFromCheckout() {
  if (state.order.status === "sending") return setAnnouncement("Order is sending. Please wait.");
  state.order = { status: "idle", error: "", orderId: "", idempotencyKey: createId() };
  state.screen = "preferences";
  state.keypad.focusId = "pref-next";
  state.preferenceIndex = 0;
  resetInferenceState();
  render();
  if (state.mode === "normal") startNormalPreferenceSession();
}

function keypadScroll(direction) {
  window.scrollBy({ top: direction * Math.round(window.innerHeight * 0.65), behavior: "smooth" });
  setAnnouncement(direction > 0 ? "Scrolled down." : "Scrolled up.");
}

function moveFocus(direction) {
  let focusables = availableFocusableEntries();
  if (!focusables.length) return direction === "up" ? keypadScroll(-1) : direction === "down" ? keypadScroll(1) : setAnnouncement("No available control.");
  if (!focusables.some(entry => entry.id === state.keypad.focusId)) {
    state.keypad.focusId = focusables[0].id;
    render();
    return setAnnouncement(`${focusables[0].label} focused.`);
  }
  const current = focusables.find(entry => entry.id === state.keypad.focusId);
  if (!current || !focusables.length) return;
  const currentCenter = rectCenter(current.rect);
  const candidates = focusables.filter(entry => {
    if (entry.id === current.id) return false;
    const center = rectCenter(entry.rect);
    if (direction === "up") return center.y < currentCenter.y - 4;
    if (direction === "down") return center.y > currentCenter.y + 4;
    if (direction === "left") return center.x < currentCenter.x - 4;
    if (direction === "right") return center.x > currentCenter.x + 4;
    return false;
  });
  if (!candidates.length) {
    if (direction === "up") return keypadScroll(-1);
    if (direction === "down") return keypadScroll(1);
    return setAnnouncement("No control in that direction.");
  }
  candidates.sort((a, b) => {
    const centerA = rectCenter(a.rect);
    const centerB = rectCenter(b.rect);
    const primaryA = direction === "left" || direction === "right" ? Math.abs(centerA.x - currentCenter.x) : Math.abs(centerA.y - currentCenter.y);
    const primaryB = direction === "left" || direction === "right" ? Math.abs(centerB.x - currentCenter.x) : Math.abs(centerB.y - currentCenter.y);
    const secondaryA = direction === "left" || direction === "right" ? Math.abs(centerA.y - currentCenter.y) : Math.abs(centerA.x - currentCenter.x);
    const secondaryB = direction === "left" || direction === "right" ? Math.abs(centerB.y - currentCenter.y) : Math.abs(centerB.x - currentCenter.x);
    return primaryA - primaryB || secondaryA - secondaryB;
  });
  state.keypad.focusId = candidates[0].id;
  render();
  scrollFocusedIntoView();
  setAnnouncement(`${candidates[0].label} focused.`);
}

function activateFocused() {
  const entry = availableFocusableEntries().find(candidate => candidate.id === state.keypad.focusId);
  if (!entry) return setAnnouncement("That control is unavailable.");
  entry.action();
}

function goBack() {
  if (state.screen === "mode") return setAnnouncement("Choose Normal or Deaf.");
  if (state.screen === "select") return resetOrder();
  if (state.screen === "preferences") return backToSelect();
  if (state.screen === "checkout") return editPreferencesFromCheckout();
  if (state.screen === "success") return resetOrder();
}

function handleKeypadKey(event) {
  const match = /^Numpad([0-9])$/.exec(event.code || "");
  event.preventDefault();
  event.stopImmediatePropagation();
  if (!match) {
    if (event.type === "keydown") setAnnouncement("Only the physical numeric keypad works on this kiosk.");
    return;
  }
  const number = Number(match[1]);
  if (number === 8) return moveFocus("up");
  if (number === 2) return moveFocus("down");
  if (number === 4) return moveFocus("left");
  if (number === 6) return moveFocus("right");
  if (number === 5) return activateFocused();
  if (number === 0) return goBack();
  setAnnouncement("Use 8 up, 2 down, 4 left, 6 right, 5 select, or 0 back.");
}

function suppressPointerInput(event) {
  event.preventDefault();
  event.stopImmediatePropagation();
}

["pointerdown", "pointerup", "pointermove", "mousedown", "mouseup", "mousemove", "click", "dblclick", "touchstart", "touchmove", "touchend", "contextmenu", "wheel"].forEach(type => {
  document.addEventListener(type, suppressPointerInput, { capture: true, passive: false });
});
document.addEventListener("keydown", handleKeypadKey, { capture: true });

function renderCheckout() {
  stopCameraAndInference();
  const tax = subtotal() * 0.0825;
  const grand = subtotal() + tax;
  ensureFocus();
  app.innerHTML = `<section class="shell ${state.mode === "blind" ? "voice-driven" : ""}">
    ${topLine("Checkout")}
    ${keypadLegend()}
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
          <button class="btn green ${focusAttrs("checkout-send")} data-action="send" ${state.order.status === "sending" ? "disabled" : ""}>${state.order.status === "sending" ? "Sending…" : "Send Order"}</button>
          <button class="btn ${focusAttrs("checkout-edit")} data-action="back-preferences" ${state.order.status === "sending" ? "disabled" : ""}>← Edit preferences</button>
          <button class="btn red ${focusAttrs("checkout-reset")} data-action="reset" ${state.order.status === "sending" ? "disabled" : ""}>Reset</button>
        </div>
      </div>
    </div>
  </section>${toastMarkup()}`;
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
  if (!canSubmitOrder()) return;
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
  ensureFocus();
  app.innerHTML = `<section class="success"><div class="success-card">
    ${keypadLegend()}
    <div class="success-icon">✅</div>
    <h1>Order received.</h1>
    <p class="lead">Confirmation ${escapeHtml(state.order.orderId)}. Thank you.</p>
    <button class="btn primary ${focusAttrs("success-new")} data-action="reset">New Order</button>
  </div></section>`;
}

function stopCameraAndInference() {
  stopInferenceLoop();
  abortActiveInferenceFetch();
  releaseInferenceSession();
  if (state.stream) state.stream.getTracks().forEach(track => track.stop());
  state.stream = null;
  state.cameraStatus = "idle";
}

function abortActiveInferenceFetch() {
  if (!inferAbortController) return;
  try {
    inferAbortController.abort();
  } catch {
    /* best effort only */
  }
}

function releaseInferenceSession() {
  if (!window.fetch) return;
  const rawScanId = state.infer.scanId;
  if (!rawScanId || releasedInferenceScanId === rawScanId) return;
  releasedInferenceScanId = rawScanId;
  const scanId = encodeURIComponent(rawScanId);
  try {
    fetch(`${API_CONFIG.inferEndpoint}/release?scan_id=${scanId}`, {
      method: "POST",
      credentials: "same-origin",
      keepalive: true
    }).catch(() => {});
  } catch {
    /* best effort only */
  }
}

function resetOrder() {
  if (state.order.status === "sending") return setAnnouncement("Order is sending. Please wait.");
  stopWakeListener();
  stopLiveSession();
  stopCameraAndInference();
  state.mode = "choice";
  state.screen = "mode";
  resetKeypadSelection();
  state.category = "All";
  state.items = [];
  state.preferenceIndex = 0;
  state.toast = "";
  window.clearTimeout(showToast.timer);
  resetInferenceState();
  state.order = { status: "idle", error: "", orderId: "", idempotencyKey: createId() };
  render();
  ensureWakeListener();
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
ensureWakeListener();
