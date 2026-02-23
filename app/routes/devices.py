from flask import Blueprint, jsonify, request
from datetime import datetime, timedelta, timezone
import app.extensions as extensions
from app.utils.logger import logger

devices_bp = Blueprint("devices", __name__)

@devices_bp.route("/devices", methods=["GET"])
def get_devices():
    city_filter = request.args.get("city")
    lab_filter = request.args.get("lab")
    status_filter = request.args.get("status")
    search = request.args.get("search")

    try:
        now = datetime.now(timezone.utc)
        
        # --- SELF-HEALING: Cleanup Stale 'Online' Statuses ---
        stale_threshold_db = now - timedelta(seconds=60)
        try:
            extensions.supabase.table("devices") \
                .update({"status": "offline"}) \
                .eq("status", "online") \
                .lt("last_seen", stale_threshold_db.isoformat()) \
                .execute()
        except Exception as e:
            logger.error(f"Status Cleanup Error: {e}")

        query = extensions.supabase.table("devices").select("*")
        if city_filter:
            query = query.eq("city", city_filter)
        if lab_filter:
            query = query.eq("lab_name", lab_filter)
        if search:
            query = query.ilike("pc_name", f"%{search}%")

        res = query.execute()
        total_found = res.data if res.data else []
        
        raw_devices = total_found
        
        threshold = now - timedelta(seconds=60)
        
        processed_devices = []
        for d in raw_devices:
            last_seen_val = d.get("last_seen")
            is_truly_online = False
            
            if d.get("status") == "online" and last_seen_val:
                try:
                    ls_dt = datetime.fromisoformat(last_seen_val.replace('Z', '+00:00'))
                    if ls_dt > threshold:
                        is_truly_online = True
                except: pass
            
            if status_filter == 'online' and not is_truly_online:
                continue
            if status_filter == 'offline' and is_truly_online:
                continue
                
            d['_is_online'] = is_truly_online
            processed_devices.append(d)

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
        
        stale_threshold_db = now - timedelta(seconds=60)
        try:
            extensions.supabase.table("devices") \
                .update({"status": "offline"}) \
                .eq("status", "online") \
                .lt("last_seen", stale_threshold_db.isoformat()) \
                .execute()
        except: pass

        res = extensions.supabase.table("devices").select("*").eq("system_id", hid).execute()
        if not res.data:
            return jsonify({"error": "Device not found"}), 404
        
        device = res.data[0]

        history_res = extensions.supabase.table("device_daily_history") \
            .select("*") \
            .eq("device_id", hid) \
            .order("history_date", desc=True) \
            .limit(7) \
            .execute()
        
        history = history_res.data if history_res.data else []

        today_utc = datetime.now(timezone.utc).date().isoformat()
        
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
            "lab_name": data.get("lab_name"),
            "tehsil": data.get("tehsil")
        }).eq("system_id", hid).execute()
        
        return jsonify({"status": "updated", "device": res.data[0]})
    except Exception as e:
        logger.error(f"Error updating device: {e}")
        return jsonify({"error": str(e)}), 500
