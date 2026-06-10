"""
ChainPilot Monitor — runs continuously, triggers pipeline on disruption detection.
This is what makes ChainPilot an AUTONOMOUS agent: it acts without being asked.
"""
import asyncio, time, sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import POLL_INTERVAL_SECONDS
from backend.agent import detect_disruptions, run_disruption_pipeline
import mock_data

# Track active events to avoid duplicate pipelines
_active_events: set = set()
_event_log: list = []


def _event_key(event: dict) -> str:
    return f"{event.get('type')}:{event.get('sku', event.get('supplier', 'unknown'))}"


async def monitor_loop(on_event=None, on_poll=None):
    """
    Async monitor loop — polls every POLL_INTERVAL_SECONDS.

    Args:
        on_event: callback(event, pipeline_result) when disruption found
        on_poll: callback(timestamp, status) on each poll cycle
    """
    print(f"[ChainPilot Monitor] Started. Polling every {POLL_INTERVAL_SECONDS}s...")

    while True:
        ts = time.strftime("%H:%M:%S")
        try:
            # ── Real-time simulation: drift data on every tick ────────────────
            mock_data.drift_tick()

            events = detect_disruptions()

            if on_poll:
                on_poll(ts, f"Scanned — {len(events)} disruption(s) detected")

            new_events = [e for e in events if _event_key(e) not in _active_events]

            for event in new_events:
                key = _event_key(event)
                _active_events.add(key)
                print(f"[{ts}] DISRUPTION DETECTED: {event}")

                # Run pipeline in background
                if on_event:
                    asyncio.create_task(_run_pipeline_async(event, on_event))
                else:
                    result = run_disruption_pipeline(event)
                    _event_log.append({"event": event, "result": result})

        except Exception as e:
            print(f"[{ts}] Monitor error: {e}")
            if on_poll:
                on_poll(ts, f"Error: {e}")

        await asyncio.sleep(POLL_INTERVAL_SECONDS)


async def _run_pipeline_async(event: dict, callback):
    """Run pipeline in thread pool to not block monitor loop."""
    import concurrent.futures
    loop = asyncio.get_running_loop()
    with concurrent.futures.ThreadPoolExecutor() as pool:
        result = await loop.run_in_executor(pool, run_disruption_pipeline, event)
    _event_log.append({"event": event, "result": result})
    if callback:
        callback(event, result)


def get_event_log():
    return _event_log


def clear_active_events():
    """Reset for demo purposes — allows re-triggering same event."""
    _active_events.clear()


def clear_event_log():
    _event_log.clear()


if __name__ == "__main__":
    asyncio.run(monitor_loop())
