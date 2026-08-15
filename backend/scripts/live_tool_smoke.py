import asyncio, os, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import gemini_api_key_loaded, gemini_model, load_settings
from app.voice import tool_declarations


def sdk_tools(types):
    declarations = tool_declarations()[0]["function_declarations"]
    return [types.Tool(functionDeclarations=[types.FunctionDeclaration(name=d["name"], description=d["description"], parametersJsonSchema=d["parameters"]) for d in declarations])]


async def main():
    load_settings()
    if not gemini_api_key_loaded():
        print("Gemini Live tool smoke: skipped (key not configured)")
        return 0
    try:
        from google import genai
        from google.genai import types
        client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
        config = types.LiveConnectConfig(response_modalities=["AUDIO"], system_instruction='Call activate_blind_mode when the user says exactly "HEY KAISIGN". Do not speak before calling the tool.', tools=sdk_tools(types))
        async with client.aio.live.connect(model=gemini_model(), config=config) as session:
            try:
                await session.send_client_content(turns=types.Content(role="user", parts=[types.Part(text="HEY KAISIGN")]), turn_complete=True)
            except AttributeError:
                await session.send(input="HEY KAISIGN", end_of_turn=True)
            deadline = asyncio.get_running_loop().time() + 20
            ok = False
            while not ok and asyncio.get_running_loop().time() < deadline:
                remaining = max(0.1, deadline - asyncio.get_running_loop().time())
                async def receive_one_turn():
                    async for response in session.receive():
                        calls = getattr(getattr(response, "tool_call", None), "function_calls", None) or []
                        for call in calls:
                            if getattr(call, "name", "") == "activate_blind_mode":
                                await session.send_tool_response(function_responses=[types.FunctionResponse(id=getattr(call, "id", None), name="activate_blind_mode", response={"ok": True})])
                                return True
                    return False
                try:
                    ok = await asyncio.wait_for(receive_one_turn(), timeout=remaining)
                except asyncio.TimeoutError:
                    break
                if not ok:
                    await asyncio.sleep(0.05)
            if ok:
                print("Gemini Live tool smoke: success")
                return 0
        print("Gemini Live tool smoke: failed (no tool call)")
        return 1
    except ImportError:
        print("Gemini Live tool smoke: failed (sdk unavailable)")
        return 1
    except Exception:
        print("Gemini Live tool smoke: failed (provider/connectivity/config)")
        return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
