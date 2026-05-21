#!/usr/bin/env python3
import argparse
import asyncio
import copy
import json
import logging
import uuid

import websockets

from protocols import (
    EventType,
    MsgType,
    finish_connection,
    finish_session,
    receive_message,
    start_connection,
    start_session,
    task_request,
    wait_for_event,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def get_resource_id(voice: str) -> str:
    if voice.startswith("S_"):
        return "volc.megatts.default"
    return "volc.service_type.10029"


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--appid", required=True, help="APP ID")
    parser.add_argument("--access_token", required=True, help="Access Token")
    parser.add_argument("--resource_id", default="", help="Resource ID")
    parser.add_argument("--text", required=True, help="Text to convert")
    parser.add_argument("--voice_type", default="", required=True, help="Voice type")
    parser.add_argument("--encoding", default="mp3", help="Output file encoding")
    parser.add_argument(
        "--endpoint",
        default="wss://openspeech.bytedance.com/api/v3/tts/bidirection",
        help="WebSocket endpoint URL",
    )

    args = parser.parse_args()

    # Connect to server
    headers = {
        "X-Api-App-Key": args.appid,
        "X-Api-Access-Key": args.access_token,
        "X-Api-Resource-Id": (
            args.resource_id if args.resource_id else get_resource_id(args.voice_type)
        ),
        "X-Api-Connect-Id": str(uuid.uuid4()),
    }

    logger.info(f"Connecting to {args.endpoint} with headers: {headers}")
    websocket = await websockets.connect(
        args.endpoint, additional_headers=headers, max_size=10 * 1024 * 1024
    )
    logger.info(
        f"Connected to WebSocket server, Logid: {websocket.response.headers['x-tt-logid']}",
    )

    try:
        # Start connection
        await start_connection(websocket)
        await wait_for_event(
            websocket, MsgType.FullServerResponse, EventType.ConnectionStarted
        )

        # Process each sentence
        sentences = args.text.split("。")
        audio_received = False

        for i, sentence in enumerate(sentences):
            if not sentence:
                continue

            # every session can have different parameters
            base_request = {
                "user": {
                    "uid": str(uuid.uuid4()),
                },
                "namespace": "BidirectionalTTS",
                "req_params": {
                    "speaker": args.voice_type,
                    "audio_params": {
                        "format": args.encoding,
                        "sample_rate": 24000,
                        "enable_timestamp": True,
                    },
                    "additions": json.dumps(
                        {
                            "disable_markdown_filter": False,
                        }
                    ),
                },
            }

            # Start session
            start_session_request = copy.deepcopy(base_request)
            start_session_request["event"] = EventType.StartSession
            session_id = str(uuid.uuid4())
            await start_session(
                websocket, json.dumps(start_session_request).encode(), session_id
            )
            await wait_for_event(
                websocket, MsgType.FullServerResponse, EventType.SessionStarted
            )

            # Send characters one by one
            async def send_chars():
                for char in sentence:
                    synthesis_request = copy.deepcopy(base_request)
                    synthesis_request["event"] = EventType.TaskRequest
                    synthesis_request["req_params"]["text"] = char
                    await task_request(
                        websocket, json.dumps(synthesis_request).encode(), session_id
                    )
                    await asyncio.sleep(0.005)  # 5ms delay between characters

                await finish_session(websocket, session_id)

            # Start sending characters in background
            send_task = asyncio.create_task(send_chars())

            # Receive audio data
            audio_data = bytearray()
            while True:
                msg = await receive_message(websocket)

                if msg.type == MsgType.FullServerResponse:
                    if msg.event == EventType.SessionFinished:
                        break
                elif msg.type == MsgType.AudioOnlyServer:
                    if not audio_received and len(audio_data) > 0:
                        audio_received = True
                    audio_data.extend(msg.payload)
                else:
                    raise RuntimeError(f"TTS conversion failed: {msg}")

            # Wait for send_chars to complete
            await send_task

            # Save audio file if we received any data
            if audio_data:
                filename = f"{args.voice_type}_session_{i}.{args.encoding}"
                with open(filename, "wb") as f:
                    f.write(audio_data)
                logger.info(f"Audio received: {len(audio_data)}, saved to {filename}")

        if not audio_received:
            raise RuntimeError("No audio data received")

    finally:
        # Finish connection
        await finish_connection(websocket)
        msg = await wait_for_event(
            websocket, MsgType.FullServerResponse, EventType.ConnectionFinished
        )
        await websocket.close()
        logger.info("Connection closed")


if __name__ == "__main__":
    asyncio.run(main())
