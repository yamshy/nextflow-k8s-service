#!/usr/bin/env python3
"""Simple WebSocket test to verify message delivery."""

import asyncio
import json
import websockets

async def test_websocket():
    uri = "ws://localhost:8000/api/v1/pipeline/stream"
    print(f"Connecting to {uri}...")

    try:
        async with websockets.connect(uri) as websocket:
            print("✓ Connected to WebSocket")
            print("Waiting for messages (10 second timeout)...")

            # Just listen for 10 seconds and print any messages
            end_time = asyncio.get_event_loop().time() + 10
            message_count = 0

            while asyncio.get_event_loop().time() < end_time:
                try:
                    # Non-blocking receive with short timeout
                    message = await asyncio.wait_for(websocket.recv(), timeout=0.5)
                    data = json.loads(message)
                    message_count += 1
                    print(f"\n✅ Message #{message_count}:")
                    print(f"  Type: {data.get('type')}")
                    print(f"  Run ID: {data.get('run_id')}")
                    if 'data' in data:
                        print(f"  Data keys: {list(data['data'].keys())}")
                except asyncio.TimeoutError:
                    # No message in 0.5s, continue
                    pass

            print(f"\n\nTotal messages received: {message_count}")
            return message_count > 0

    except Exception as e:
        print(f"❌ Error: {e}")
        return False

if __name__ == "__main__":
    success = asyncio.run(test_websocket())
    print(f"\nWebSocket test: {'PASSED' if success else 'FAILED'}")