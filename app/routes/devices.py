from flask import Blueprint, jsonify, request
from datetime import datetime, timedelta, timezone
import app.extensions as extensions
from app.utils.logger import logger

devices_bp = Blueprint("devices", __name__)

@devices_bp.route("/devices", methods=["GET"])
def get_devices():
    city_filter = request.args.get("city")
    lab_filter = request.args.get("lab") # ADDED: Get lab parameter
    status_filter = request.args.get("status")
    search = request.args.get("search")

    try:
        now = datetime.now(timezone.utc)
        
        # --- SELF-HEALING: Cleanup Stale 'Online' Statuses ---
        # Match dashboard threshold: 60 seconds
        stale_threshold_db = now - timedelta(seconds=60)
        try:
            extensions.supabase.table("devices") \
                .update({"status": "offline"}) \
                .eq("status", "online") \
                .lt("last_seen", stale_threshold_db.isoformat()) \
                .execute()
        except Exception as e:
            logger.error(f"Status Cleanup Error: {e}")

        # Fetch all candidates
        query = extensions.supabase.table("devices").select("*")
        if city_filter:
            query = query.eq("city", city_filter)
        if lab_filter: # ADDED: Apply lab filter
            query = query.eq("lab_name", lab_filter)
        if search:
            query = query.ilike("pc_name", f"%{search}%")

        res = query.execute()
        total_found = res.data if res.data else []
        
        # Show all devices in the inventory (both registered and unregistered placeholders)
        raw_devices = total_found
        
        threshold = now - timedelta(seconds=60)
        
        processed_devices = []
        for d in raw_devices:
            # Determine real status
            # A device is online ONLY if it has 'online' status AND a fresh heartbeat
            last_seen_val = d.get("last_seen")
            is_truly_online = False
            
            if d.get("status") == "online" and last_seen_val:
                try:
                    # Parse as aware datetime
                    ls_dt = datetime.fromisoformat(last_seen_val.replace('Z', '+00:00'))
                    if ls_dt > threshold:
                        is_truly_online = True
                except: pass
            
            # Apply status filter if requested
            if status_filter == 'online' and not is_truly_online:
                continue
            if status_filter == 'offline' and is_truly_online:
                continue
                
            # Add a 'is_online' helper for easier frontend sorting
            d['_is_online'] = is_truly_online
            processed_devices.append(d)

        # Sort: Online first, then by name (Handle None pc_names for unregistered slots)
        processed_devices.sort(key=lambda x: (not x['_is_online'], x.get('pc_name') or ''))

        return jsonify({
            "devices": processed_devices,
            "server_time": now.replace(tzinfo=None).isoformat() + "Z"
        })
    except Exception as e:
        logger.error(f"Error fetching devices: {e}")
        return jsonify({"error": str(e)}), 500

@devices_bp.route("/devices/<hid>", methods=["GET"])
def get_device_detail(hid):
    try:
        now = datetime.now(timezone.utc)
        
        # --- SELF-HEALING: Cleanup Stale 'Online' Statuses (Universal) ---
        stale_threshold_db = now - timedelta(seconds=60)
        try:
            extensions.supabase.table("devices") \
                .update({"status": "offline"}) \
                .eq("status", "online") \
                .lt("last_seen", stale_threshold_db.isoformat()) \
                .execute()
        except: pass

        # 1. PC Settings & Current State
        res = extensions.supabase.table("devices").select("*").eq("system_id", hid).execute()
        if not res.data:
            return jsonify({"error": "Device not found"}), 404
        
        device = res.data[0]

        # 2. Daily Summary History (Last 7 days)
        history_res = extensions.supabase.table("device_daily_history") \
            .select("*") \
            .eq("device_id", hid) \
            .order("history_date", desc=True) \
            .limit(7) \
            .execute()
        
        history = history_res.data if history_res.data else []

        # 3. Today's Session Frequency (Professional: Count sessions active today)
        today_utc = datetime.now(timezone.utc).date().isoformat()
        
        # Count sessions that:
        # 1. Started today OR
        # 2. Are still active (end_time is null)
        session_res = extensions.supabase.table("device_sessions") \
            .select("id", count='exact') \
            .eq("device_id", hid) \
            .or_(f"start_time.gte.{today_utc}T00:00:00Z,end_time.is.null") \
            .execute()
        
        session_count = session_res.count if session_res.count is not None else 0

        return jsonify({
            "device": device,
            "history": history,
            "session_count": session_count,
            "server_time": now.replace(tzinfo=None).isoformat() + "Z"
        })

    except Exception as e:
        logger.error(f"Error in detail: {e}")
        return jsonify({"error": str(e)}), 500

@devices_bp.route("/devices/<hid>", methods=["PATCH"])
def update_device(hid):
    data = request.get_json()
    try:
        res = extensions.supabase.table("devices").update({
            "pc_name": data.get("pc_name"),
            "city": data.get("city"),
            "lab_name": data.get("lab_name")
        }).eq("system_id", hid).execute()
        
        return jsonify({"status": "updated", "device": res.data[0]})
    except Exception as e:
        logger.error(f"Error updating device: {e}")
        return jsonify({"error": str(e)}), 500

