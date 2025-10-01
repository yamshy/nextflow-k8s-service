#!/usr/bin/env python3
"""Full WebSocket test with concurrent run monitoring."""

import asyncio
import json
import urllib.request
import websockets

def start_demo_run():
    """Start a demo run via API."""
    data = json.dumps({"batch_count": 2, "triggered_by": "WebSocket full test"}).encode('utf-8')
    req = urllib.request.Request(
        'http://localhost:8000/api/v1/demo/run',
        data=data,
        headers={'Content-Type': 'application/json'}
    )

    with urllib.request.urlopen(req) as response:
        result = json.loads(response.read().decode('utf-8'))
        return result

async def monitor_websocket(run_id):
    """Monitor WebSocket for a specific run."""
    uri = "ws://localhost:8000/api/v1/pipeline/stream"
    print(f"📡 Connecting to WebSocket for run {run_id}...")

    message_stats = {
        'status': 0,
        'log': 0,
        'progress': 0,
        'complete': 0,
        'error': 0,
        'other': 0
    }

    try:
        async with websockets.connect(uri) as websocket:
            print("✓ WebSocket connected\n")

            while True:
                try:
                    message = await asyncio.wait_for(websocket.recv(), timeout=120)
                    data = json.loads(message)

                    # Filter for our run
                    if data.get('run_id') != run_id and data.get('type') != 'status':
                        continue

                    msg_type = data.get('type', 'other')
                    message_stats[msg_type if msg_type in message_stats else 'other'] += 1

                    # Display based on type
                    if msg_type == 'status':
                        status_data = data.get('data', {})
                        if status_data.get('run', {}).get('run_id') == run_id:
                            print(f"📊 Status: {status_data.get('run', {}).get('status')}")

                    elif msg_type == 'log':
                        log_data = data.get('data', {})
                        logs = log_data.get('logs', [])
                        if logs:
                            print(f"📝 Received {len(logs)} log lines")
                            # Show last log line
                            last_log = logs[-1].get('line', '')[:100] if logs else ''
                            if last_log:
                                print(f"   Last: {last_log}")

                    elif msg_type == 'progress':
                        progress_data = data.get('data', {})
                        print(f"⏳ Progress: {progress_data.get('percent', 0)}%")

                    elif msg_type == 'complete':
                        complete_data = data.get('data', {})
                        print(f"\n✅ Run completed!")
                        print(f"   Status: {complete_data.get('status')}")

                        # Check for duration_seconds at top level
                        duration = complete_data.get('duration_seconds')
                        if duration is not None:
                            print(f"   ✅ Duration provided: {duration:.2f}s")
                        else:
                            print(f"   ❌ Duration NOT provided in message")

                        # Calculate manually from timestamps if present
                        run_data = complete_data.get('run', {})
                        if run_data.get('started_at') and run_data.get('finished_at'):
                            import datetime
                            start = datetime.datetime.fromisoformat(run_data['started_at'].replace('Z', '+00:00'))
                            end = datetime.datetime.fromisoformat(run_data['finished_at'].replace('Z', '+00:00'))
                            calc_duration = (end - start).total_seconds()
                            print(f"   📊 Calculated from timestamps: {calc_duration:.2f}s")

                        break

                    elif msg_type == 'error':
                        error_data = data.get('data', {})
                        print(f"\n❌ Error: {error_data.get('message')}")
                        break

                except asyncio.TimeoutError:
                    print("\n⏱️ Timeout waiting for messages")
                    break

    except Exception as e:
        print(f"\n❌ WebSocket error: {e}")

    # Summary
    print("\n" + "=" * 40)
    print("MESSAGE STATISTICS")
    print("=" * 40)
    for msg_type, count in message_stats.items():
        if count > 0:
            print(f"{msg_type:10}: {count}")

    return sum(message_stats.values()) > 0

async def main():
    """Main test function."""
    print("Starting full WebSocket test...")
    print("-" * 40)

    # Start a run
    print("1️⃣ Starting demo run...")
    result = start_demo_run()
    run_id = result['run_id']
    print(f"   Run ID: {run_id}")
    print(f"   Status: {result['status']}")
    print(f"   Attached: {result['attached']}")
    print()

    # Monitor via WebSocket
    print("2️⃣ Monitoring via WebSocket...")
    success = await monitor_websocket(run_id)

    print(f"\n{'✅' if success else '❌'} WebSocket functionality: {'WORKING' if success else 'NOT WORKING'}")

if __name__ == "__main__":
    asyncio.run(main())