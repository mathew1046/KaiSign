import asyncio, base64, os, secrets, time
from urllib.parse import urlparse
from uuid import UUID

from fastapi import WebSocket, WebSocketDisconnect, status

from .config import gemini_model
from .voice import LiveCart, LiveContext, VoiceMode, make_envelope, menu_context, system_prompt, tool_declarations, validate_payload

MAX_AUDIO_BYTES = 96_000
MAX_AUDIO_PER_10S = 80
IDLE_SECONDS = 90
MAX_SESSION_SECONDS = 900
MAX_MESSAGE_CHARS = 140_000
MAX_ACTIVE_SESSIONS = int(os.getenv("GEMINI_LIVE_MAX_SESSIONS", "4"))
SESSION_COOKIE = "kiosk_session"
_registry_lock = asyncio.Lock()
_active_connections: dict[str, dict[str, object]] = {}


def _generic_error(code="live_error", message="Voice ordering is temporarily unavailable."):
    return {"type": "error", "code": code, "message": message}


def _origin_allowed(websocket: WebSocket) -> bool:
    origin = websocket.headers.get("origin")
    host = websocket.headers.get("host")
    if not origin or not host:
        return False
    allowed = {x.strip() for x in os.getenv("LIVE_WS_ALLOWED_ORIGINS", "").split(",") if x.strip()}
    if origin in allowed:
        return True
    parsed = urlparse(origin)
    return parsed.netloc == host and parsed.scheme in {"http", "https"}


async def reserve_live_connection(kiosk_key: str | None, client_session_id: str | None = None):
    if not kiosk_key:
        return None
    async with _registry_lock:
        if kiosk_key in _active_connections or len(_active_connections) >= MAX_ACTIVE_SESSIONS:
            return None
        token = secrets.token_urlsafe(16)
        _active_connections[kiosk_key] = {"token": token, "client_session_id": client_session_id, "started": time.monotonic()}
        return token


async def bind_live_connection(kiosk_key: str | None, token: str | None, client_session_id: str):
    if not kiosk_key or not token:
        return False
    async with _registry_lock:
        row = _active_connections.get(kiosk_key)
        if not row or row.get("token") != token:
            return False
        row["client_session_id"] = client_session_id
        return True


async def owns_live_connection(kiosk_key: str | None, token: str | None) -> bool:
    if not kiosk_key or not token:
        return False
    async with _registry_lock:
        row = _active_connections.get(kiosk_key)
        return bool(row and row.get("token") == token)


async def release_live_connection(kiosk_key: str | None, token: str | None):
    if not kiosk_key or not token:
        return
    async with _registry_lock:
        row = _active_connections.get(kiosk_key)
        if row and row.get("token") == token:
            _active_connections.pop(kiosk_key, None)


def _audio_parts(response):
    sc = getattr(response, "server_content", None)
    turn = getattr(sc, "model_turn", None) if sc else None
    for part in getattr(turn, "parts", []) or []:
        blob = getattr(part, "inline_data", None)
        data = getattr(blob, "data", None) if blob else None
        mime = getattr(blob, "mime_type", "audio/pcm;rate=24000") if blob else "audio/pcm;rate=24000"
        if data:
            rate = 24000
            if "rate=" in mime:
                try: rate = int(mime.split("rate=", 1)[1].split(";", 1)[0])
                except Exception: pass
            yield data, rate, mime


def _sdk_tools(types):
    declarations = tool_declarations()[0]["function_declarations"]
    return [types.Tool(functionDeclarations=[types.FunctionDeclaration(name=d["name"], description=d["description"], parametersJsonSchema=d["parameters"]) for d in declarations])]


async def maybe_send_blind_opening_turn(session, types, mode: VoiceMode):
    if mode != VoiceMode.blind:
        return False
    prompt = "Begin the blind ordering session now with the required greeting question only."
    try:
        await session.send_client_content(turns=types.Content(role="user", parts=[types.Part(text=prompt)]), turn_complete=True)
    except AttributeError:
        await session.send(input=prompt, end_of_turn=True)
    return True


def is_terminal_wake_activation(mode: VoiceMode, action_value: str) -> bool:
    return mode == VoiceMode.wake and action_value == "activate_blind_mode"


async def live_ws(websocket: WebSocket):
    kiosk_key = websocket.cookies.get(SESSION_COOKIE)
    owner_token = None
    if not _origin_allowed(websocket):
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION); return
    owner_token = await reserve_live_connection(kiosk_key)
    if not owner_token:
        await websocket.close(code=status.WS_1013_TRY_AGAIN_LATER); return
    try:
        await websocket.accept()
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            await websocket.send_json(_generic_error("not_configured", "Voice ordering is not configured."))
            await release_live_connection(kiosk_key, owner_token)
            return
        first_text = await asyncio.wait_for(websocket.receive_text(), timeout=15)
        if len(first_text) > MAX_MESSAGE_CHARS: raise ValueError("message too large")
        import json
        first = json.loads(first_text)
        if first.get("type") != "start": raise ValueError("start required")
        mode = VoiceMode(first.get("mode")); session_id = UUID(str(first.get("session_id")))
        if not await bind_live_connection(kiosk_key, owner_token, str(session_id)):
            await release_live_connection(kiosk_key, owner_token)
            return
        context = LiveContext.model_validate(first.get("context") or {})
        cart = LiveCart(context)
    except Exception:
        try: await websocket.send_json(_generic_error("bad_start", "Invalid voice session start."))
        except Exception: pass
        await release_live_connection(kiosk_key, owner_token)
        try: await websocket.close()
        except Exception: pass
        return
    try:
        from google import genai
        from google.genai import types
        client = genai.Client(api_key=api_key)
        config_args = {"response_modalities": ["AUDIO"], "system_instruction": system_prompt(mode, context) + "\nMENU=" + str(menu_context()) + "\nINITIAL_STATE=" + str(cart.snapshot().model_dump(mode="json")), "tools": _sdk_tools(types)}
        try:
            config_args["speech_config"] = types.SpeechConfig(
                language_code=os.getenv("GEMINI_LIVE_LANGUAGE", "en-US"),
                voice_config=types.VoiceConfig(prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=os.getenv("GEMINI_LIVE_VOICE", "Kore"))),
            )
        except Exception:
            pass
        config = types.LiveConnectConfig(**config_args)
        async with client.aio.live.connect(model=gemini_model(), config=config) as session:
            await websocket.send_json({"type": "ready", "mode": mode.value})
            await maybe_send_blind_opening_turn(session, types, mode)
            started = last = time.monotonic(); window_start = started; window_count = 0; stop = asyncio.Event()
            async def browser_to_gemini():
                nonlocal last, window_start, window_count
                try:
                    while not stop.is_set():
                        now = time.monotonic()
                        if now - started > MAX_SESSION_SECONDS or now - last > IDLE_SECONDS: break
                        try:
                            raw_msg = await asyncio.wait_for(websocket.receive_text(), timeout=5)
                            if len(raw_msg) > MAX_MESSAGE_CHARS: continue
                            import json
                            msg = json.loads(raw_msg)
                        except asyncio.TimeoutError:
                            continue
                        last = time.monotonic()
                        if msg.get("type") == "stop": break
                        if msg.get("type") == "audio":
                            if last - window_start > 10: window_start = last; window_count = 0
                            window_count += 1
                            if window_count > MAX_AUDIO_PER_10S: continue
                            try:
                                raw = base64.b64decode(msg.get("pcm16_base64", ""), validate=True)
                            except Exception:
                                continue
                            if len(raw) > MAX_AUDIO_BYTES: continue
                            await session.send_realtime_input(audio=types.Blob(data=raw, mime_type="audio/pcm;rate=16000"))
                except WebSocketDisconnect:
                    pass
                finally:
                    stop.set()
            async def gemini_to_browser():
                while not stop.is_set():
                    saw_terminal = False
                    async for response in session.receive():
                        if stop.is_set(): break
                        for data, rate, mime in _audio_parts(response):
                            if not await owns_live_connection(kiosk_key, owner_token): stop.set(); break
                            await websocket.send_json({"type": "audio", "pcm16_base64": base64.b64encode(data).decode(), "sample_rate": rate, "mime_type": mime})
                        sc = getattr(response, "server_content", None)
                        if getattr(sc, "interrupted", False): await websocket.send_json({"type": "interrupted"})
                        if getattr(response, "go_away", None):
                            await websocket.send_json({"type": "go_away"}); saw_terminal = True
                        calls = getattr(getattr(response, "tool_call", None), "function_calls", None) or []
                        if calls:
                            replies = []
                            for call in calls:
                                cid = getattr(call, "id", None); name = getattr(call, "name", ""); args = getattr(call, "args", {}) or {}
                                try:
                                    action, payload = validate_payload(name, args, mode=mode, context=context)
                                    cart.apply(action, payload)
                                    envelope = make_envelope(session_id=session_id, mode=mode, action=action, payload=payload, cart=cart)
                                    event = {"type": "wake_detected"} if action.value == "activate_blind_mode" else {"type": "state", "action": envelope.model_dump(mode="json")}
                                    if not await owns_live_connection(kiosk_key, owner_token): stop.set(); break
                                    await websocket.send_json(event)
                                    if is_terminal_wake_activation(mode, action.value):
                                        stop.set()
                                    guidance = None
                                    if action.value == "add_item":
                                        guidance = "Ask whether the user would like preferences for this item. Do not review yet."
                                    elif action.value in {"finish_customization", "continue_ordering"}:
                                        guidance = "Invite another item or ask if the user would like to review; do not call review_order unless explicitly requested."
                                    elif action.value == "select_category":
                                        guidance = "Speak only the available items in this selected category."
                                    response_body = {"ok": True, "state": envelope.state.model_dump(mode="json"), "menu": menu_context(envelope.state.category), "guidance": guidance}
                                    if action.value == "end_session": stop.set()
                                except Exception:
                                    response_body = {"ok": False, "error": "invalid_tool_args"}
                                replies.append(types.FunctionResponse(id=cid, name=name, response=response_body))
                            await session.send_tool_response(function_responses=replies)
                    if saw_terminal:
                        stop.set(); break
                    await asyncio.sleep(0.05)
            tasks = [asyncio.create_task(browser_to_gemini()), asyncio.create_task(gemini_to_browser())]
            await stop.wait()
            for task in tasks: task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
    except WebSocketDisconnect:
        return
    except Exception:
        try: await websocket.send_json(_generic_error())
        except Exception: pass
    finally:
        await release_live_connection(kiosk_key, owner_token)
        try: await websocket.close()
        except Exception: pass
