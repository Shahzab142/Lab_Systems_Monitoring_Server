from datetime import datetime, timedelta
from app.extensions import supabase
from app.utils.logger import logger

OFFLINE_AFTER_SECONDS = 15

def start_session_if_needed(device_id):
    # Check if an open session already exists
    open_session = supabase.table("sessions") \
        .select("*") \
        .eq("device_id", device_id) \
        .is_("end_time", None) \
        .limit(1) \
        .execute()

    if open_session.data:
        return

    # Start new session
    supabase.table("sessions").insert({
        "device_id": device_id,
        "start_time": datetime.utcnow().isoformat() + "Z"
    }).execute()


def close_inactive_sessions():
    # Performance throttle: only check every 30 seconds
    if hasattr(close_inactive_sessions, 'last_run'):
        if (datetime.utcnow() - close_inactive_sessions.last_run).total_seconds() < 30:
            return
            
    close_inactive_sessions.last_run = datetime.utcnow()
    
    now = datetime.utcnow()
    # Subtract seconds and ensure it's in ISO format for Supabase
    timeout_threshold = (now - timedelta(seconds=OFFLINE_AFTER_SECONDS)).isoformat() + "Z"

    # Find ONLY devices that are 'online' but have stale heartbeats
    devices = supabase.table("devices") \
        .select("id,last_seen") \
        .eq("status", "online") \
        .lt("last_seen", timeout_threshold) \
        .execute().data

    if not devices:
        return

    for device in devices:
        # 1. Close open sessions
        sessions = supabase.table("sessions") \
            .select("*") \
            .eq("device_id", device["id"]) \
            .is_("end_time", None) \
            .execute().data

        for session in sessions:
            try:
                # Add 'Z' if missing for parsing
                s_time = session["start_time"]
                if not s_time.endswith('Z') and '+' not in s_time:
                    s_time += 'Z'
                start_str = s_time.replace('Z', '+00:00')
                start = datetime.fromisoformat(start_str).replace(tzinfo=None)
                duration = max(0, int((now.replace(tzinfo=None) - start).total_seconds()))
            except Exception as e:
                logger.error(f"Error calculating duration: {e}")
                duration = 0

            supabase.table("sessions").update({
                "end_time": now.isoformat() + "Z",
                "duration_seconds": duration
            }).eq("id", session["id"]).execute()

        # 2. Mark device offline
        supabase.table("devices").update({
            "status": "offline"
        }).eq("id", device["id"]).execute()

