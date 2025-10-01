#!/usr/bin/env python3
"""Test WebSocket connection and message streaming."""

import asyncio
import json
import sys
import time
from datetime import datetime
import websockets
import urllib.request
import urllib.parse

def start_demo_run():
    """Start a demo run via API."""
    data = json.dumps({"batch_count": 2, "triggered_by": "WebSocket test"}).encode('utf-8')
    req = urllib.request.Request(
        'http://localhost:8000/api/v1/demo/run',
        data=data,
        headers={'Content-Type': 'application/json'}
    )

    with urllib.request.urlopen(req) as response:
        result = json.loads(response.read().decode('utf-8'))
        print(f"✓ Started demo run: {result['run_id']}")
        print(f"  Status: {result['status']}")
        print(f"  Attached: {result['attached']}")
        return result['run_id']

async def test_websocket():
    """Test WebSocket connection and receive messages."""
    print("Testing WebSocket functionality...")
    print("-" * 50)

    # Start a demo run first
    run_id = start_demo_run()
    print(f"\n📡 Connecting to WebSocket...")

    uri = "ws://localhost:8000/api/v1/pipeline/stream"
    message_count = 0
    message_types = set()
    start_time = time.time()

    try:
        async with websockets.connect(uri) as websocket:
            print(f"✓ WebSocket connected to {uri}")
            print(f"\n🔄 Waiting for messages (run_id: {run_id})...\n")

            while True:
                try:
                    # Set timeout to avoid infinite wait
                    message = await asyncio.wait_for(websocket.recv(), timeout=120)
                    data = json.loads(message)
                    message_count += 1
                    message_types.add(data.get('type'))

                    # Display message info
                    msg_type = data.get('type', 'unknown')
                    msg_run_id = data.get('run_id', 'none')
                    timestamp = data.get('timestamp', '')

                    print(f"[{message_count:03d}] Type: {msg_type:12} | Run: {msg_run_id} | Time: {timestamp}")

                    # Show details based on type
                    if msg_type == 'status':
                        status_data = data.get('data', {})
                        print(f"      Status: {status_data.get('status')}")

                    elif msg_type == 'log':
                        log_data = data.get('data', {})
                        logs = log_data.get('logs', [])
                        if logs:
                            print(f"      Logs: {len(logs)} lines from {log_data.get('pod_name')}")
                            # Show first log line as sample
                            if logs[0]:
                                first_line = logs[0].get('line', '')[:80]
                                print(f"      Sample: {first_line}...")

                    elif msg_type == 'progress':
                        progress_data = data.get('data', {})
                        print(f"      Progress: {progress_data.get('percent', 0)}%")

                    elif msg_type == 'complete':
                        complete_data = data.get('data', {})
                        print(f"      Final status: {complete_data.get('status')}")
                        print(f"      Duration: {complete_data.get('duration_seconds', 0):.2f}s")
                        print(f"\n✅ Run completed! Closing WebSocket...")
                        break

                    elif msg_type == 'error':
                        error_data = data.get('data', {})
                        print(f"      Error: {error_data.get('message')}")
                        print(f"\n❌ Error received! Closing WebSocket...")
                        break

                    print()  # Empty line for readability

                except asyncio.TimeoutError:
                    print("\n⏱️  Timeout waiting for messages (120s)")
                    break
                except websockets.exceptions.ConnectionClosed:
                    print("\n🔌 WebSocket connection closed by server")
                    break

    except Exception as e:
        print(f"\n❌ WebSocket error: {e}")
        return False

    # Summary
    elapsed = time.time() - start_time
    print("\n" + "=" * 50)
    print("WEBSOCKET TEST SUMMARY")
    print("=" * 50)
    print(f"✓ Messages received: {message_count}")
    print(f"✓ Message types seen: {', '.join(sorted(message_types))}")
    print(f"✓ Connection duration: {elapsed:.2f}s")
    print(f"✓ WebSocket functionality: {'WORKING' if message_count > 0 else 'NOT WORKING'}")

    return message_count > 0

async def main():
    """Main test function."""
    try:
        success = await test_websocket()
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        print("\n\n⚠️  Test interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Test failed: {e}")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())