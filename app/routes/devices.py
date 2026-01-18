from flask import Blueprint, jsonify, request
from datetime import datetime, timedelta
import app.extensions as extensions
from app.utils.logger import logger

devices_bp = Blueprint("devices", __name__)

@devices_bp.route("/devices", methods=["GET"])
def get_devices():
    city_filter = request.args.get("city")
    status_filter = request.args.get("status")
    search = request.args.get("search")

    try:
        from datetime import datetime, timezone, timedelta
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
        if search:
            query = query.ilike("pc_name", f"%{search}%")

        res = query.execute()
        total_found = res.data if res.data else []
        
        # PROFESSIONAL: Only show devices that have been REGISTERED (hardware_id is not null)
        raw_devices = [d for d in total_found if d.get("hardware_id")]
        
        threshold = now - timedelta(seconds=60)
        
        processed_devices = []
        for d in raw_devices:
            # Determine real status
            is_truly_online = False
            if d.get("status") == "online" and d.get("last_seen"):
                try:
                    # Parse as aware datetime
                    ls_dt = datetime.fromisoformat(d["last_seen"].replace('Z', '+00:00'))
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

        # Sort: Online first, then by name
        processed_devices.sort(key=lambda x: (not x['_is_online'], x.get('pc_name', '')))

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
        from datetime import timezone, timedelta
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

        return jsonify({
            "device": device,
            "history": history,
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
