#!/usr/bin/env python3
"""Standalone test: capture camera frame → send to MiMo with image → diagnose."""

import asyncio
import base64
import os
import sys
import time

from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("LAMPGO_LLM_API_KEY", "")
API_BASE = os.getenv("LAMPGO_LLM_API_BASE", "https://api.xiaomimimo.com/v1")
MODEL = os.getenv("LAMPGO_LLM_FAST_MODEL", "mimo-v2-omni")
CAMERA_PORT = os.getenv("LAMPGO_CAMERA_PORT", "0")
TIMEOUT = 90


def capture_frame(device_index: int, width: int = 640, height: int = 480, quality: int = 70) -> str | None:
    import cv2

    cap = cv2.VideoCapture(device_index)
    if not cap.isOpened():
        print(f"[ERROR] Cannot open camera {device_index}")
        return None

    cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
    for _ in range(3):
        cap.read()

    ok, frame = cap.read()
    cap.release()
    if not ok or frame is None:
        print("[ERROR] Failed to capture frame")
        return None

    ok, encoded = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
    if not ok:
        print("[ERROR] JPEG encode failed")
        return None

    payload = base64.b64encode(encoded.tobytes()).decode("ascii")
    size_kb = len(payload) / 1024
    print(f"[OK] Frame {width}x{height} q={quality}, base64: {size_kb:.0f} KB")
    return f"data:image/jpeg;base64,{payload}"


async def do_request(label: str, body: dict, timeout: int = TIMEOUT):
    import httpx

    print(f"\n{'='*60}")
    print(f"  {label}")
    print(f"{'='*60}")
    print(f"  model={body.get('model')}  timeout={timeout}s")
    if "tools" in body:
        print(f"  tools={len(body['tools'])}  tool_choice={body.get('tool_choice', 'auto')}")
    if "thinking" in body:
        print(f"  thinking={body['thinking']}")

    headers = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}

    t0 = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(f"{API_BASE}/chat/completions", json=body, headers=headers)
        elapsed = time.monotonic() - t0
        print(f"  Status: {resp.status_code}  Elapsed: {elapsed:.1f}s")
        if resp.status_code == 200:
            data = resp.json()
            msg = data["choices"][0]["message"]
            content = msg.get("content", "")
            tool_calls = msg.get("tool_calls", [])
            usage = data.get("usage", {})
            print(f"  Usage: prompt={usage.get('prompt_tokens','?')} completion={usage.get('completion_tokens','?')}")
            if content:
                print(f"  Content: {content[:300]}")
            if tool_calls:
                for tc in tool_calls:
                    print(f"  Tool: {tc['function']['name']}({tc['function']['arguments'][:200]})")
            if not content and not tool_calls:
                print(f"  (empty response)")
            return True
        else:
            print(f"  Error: {resp.text[:500]}")
            return False
    except httpx.ReadTimeout:
        elapsed = time.monotonic() - t0
        print(f"  [TIMEOUT] ReadTimeout after {elapsed:.1f}s")
        return False
    except Exception as e:
        elapsed = time.monotonic() - t0
        print(f"  [ERROR] {type(e).__name__}: {e}  ({elapsed:.1f}s)")
        return False


SIMPLE_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "finish_response",
            "description": "Return a text reply to the user.",
            "parameters": {
                "type": "object",
                "properties": {"text": {"type": "string", "description": "Reply text"}},
                "required": ["text"],
            },
        },
    }
]


def make_image_messages(image_url: str):
    return [
        {"role": "system", "content": "You are lampgo, a desk lamp robot with a camera. Reply in Chinese, be brief."},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "你看到了什么？"},
                {"type": "image_url", "image_url": {"url": image_url}},
            ],
        },
    ]


async def main():
    if not API_KEY:
        print("[ERROR] LAMPGO_LLM_API_KEY not set")
        sys.exit(1)

    print(f"API: {API_BASE}")
    print(f"Model: {MODEL}")

    # --- Test 1: text only (baseline) ---
    await do_request("Test 1: text only (baseline)", {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": "Reply briefly."},
            {"role": "user", "content": "你好"},
        ],
        "temperature": 0.3,
        "max_completion_tokens": 128,
        "thinking": {"type": "disabled"},
    })

    # --- Capture small image ---
    device = int(CAMERA_PORT) if CAMERA_PORT.isdigit() else 0
    image_url = capture_frame(device, width=640, height=480, quality=50)
    if not image_url:
        print("\n[SKIP] No camera, skipping vision tests")
        return

    # --- Test 2: image only, NO tools, NO thinking disabled ---
    await do_request("Test 2: image, no tools, default thinking", {
        "model": MODEL,
        "messages": make_image_messages(image_url),
        "temperature": 0.3,
        "max_completion_tokens": 256,
    })

    # --- Test 3: image, NO tools, thinking disabled ---
    await do_request("Test 3: image, no tools, thinking=disabled", {
        "model": MODEL,
        "messages": make_image_messages(image_url),
        "temperature": 0.3,
        "max_completion_tokens": 256,
        "thinking": {"type": "disabled"},
    })

    # --- Test 4: image + tools, tool_choice=auto ---
    await do_request("Test 4: image + tools, tool_choice=auto", {
        "model": MODEL,
        "messages": make_image_messages(image_url),
        "tools": SIMPLE_TOOLS,
        "tool_choice": "auto",
        "temperature": 0.3,
        "max_completion_tokens": 256,
        "thinking": {"type": "disabled"},
    })

    # --- Test 5: image + tools, tool_choice=required (same as agent loop) ---
    await do_request("Test 5: image + tools, tool_choice=required (agent loop)", {
        "model": MODEL,
        "messages": make_image_messages(image_url),
        "tools": SIMPLE_TOOLS,
        "tool_choice": "required",
        "temperature": 0.3,
        "max_completion_tokens": 512,
        "thinking": {"type": "disabled"},
    })

    print("\n" + "="*60)
    print("  All tests done.")
    print("="*60)


if __name__ == "__main__":
    asyncio.run(main())
