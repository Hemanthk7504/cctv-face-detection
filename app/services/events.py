import asyncio
import collections
import datetime as dt
import json
import uuid
from typing import Set

# Ring buffer for recent detection events and alerts
RECENT_EVENTS: collections.deque = collections.deque(maxlen=200)
ALERTS_LIST: collections.deque = collections.deque(maxlen=100)
SUBSCRIBERS: Set[asyncio.Queue] = set()
MAIN_LOOP: asyncio.AbstractEventLoop | None = None


def set_main_loop(loop: asyncio.AbstractEventLoop):
    global MAIN_LOOP
    MAIN_LOOP = loop


def publish_event(event: dict):
    """Publish a detection event and register alerts for suspects or anomalies."""
    if "id" not in event:
        event["id"] = int(dt.datetime.now().timestamp() * 1000)
    if "ts_iso" not in event:
        event["ts_iso"] = dt.datetime.now().isoformat()
    
    RECENT_EVENTS.appendleft(event)

    # Check if this qualifies as a Security Alert
    is_suspect = event.get("is_suspect", False)
    is_unregistered = event.get("person_role") in ("Unknown", "Unregistered")
    
    if is_suspect or is_unregistered:
        alert_id = uuid.uuid4().hex[:8]
        alert_item = {
            "id": alert_id,
            "severity": "CRITICAL" if is_suspect else "WARNING",
            "title": f"🚨 Suspect Sighted: {event.get('person_name', 'Unknown')}" if is_suspect else "⚠️ Unregistered Individual Detected",
            "person_id": event.get("person_id"),
            "person_name": event.get("person_name", "Unregistered Face"),
            "person_role": event.get("person_role", "Unknown"),
            "camera_id": event.get("camera_id"),
            "camera_name": event.get("camera_name", "Camera"),
            "point": event.get("point", "General Area"),
            "similarity": event.get("similarity", 0),
            "image": event.get("image", ""),
            "time": event.get("ts", dt.datetime.now().strftime("%H:%M:%S")),
            "date": event.get("date", dt.datetime.now().strftime("%Y-%m-%d")),
            "dismissed": False
        }
        ALERTS_LIST.appendleft(alert_item)
        event["alert"] = alert_item

    # Broadcast to active subscriber queues
    if SUBSCRIBERS:
        for q in list(SUBSCRIBERS):
            try:
                if MAIN_LOOP and not MAIN_LOOP.is_closed():
                    MAIN_LOOP.call_soon_threadsafe(q.put_nowait, event)
                else:
                    q.put_nowait(event)
            except Exception:
                pass


def get_recent_events(limit: int = 40):
    return list(RECENT_EVENTS)[:limit]


def get_alerts(limit: int = 50, severity: str | None = None):
    results = [a for a in ALERTS_LIST if not a.get("dismissed", False)]
    if severity:
        results = [a for a in results if a.get("severity") == severity.upper()]
    return results[:limit]


def dismiss_alert(alert_id: str):
    for a in ALERTS_LIST:
        if a["id"] == alert_id:
            a["dismissed"] = True
            return True
    return False


def clear_all_alerts():
    ALERTS_LIST.clear()
    return True


async def subscribe_events():
    q = asyncio.Queue(maxsize=100)
    SUBSCRIBERS.add(q)
    try:
        while True:
            ev = await q.get()
            yield f"data: {json.dumps(ev)}\n\n"
    except (asyncio.CancelledError, GeneratorExit):
        pass
    finally:
        SUBSCRIBERS.discard(q)
