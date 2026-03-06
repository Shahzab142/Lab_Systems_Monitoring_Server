from flask import Blueprint, jsonify, request
from datetime import datetime, timedelta, timezone
import app.extensions as extensions
from app.utils.logger import logger

devices_bp = Blueprint("devices", __name__)

@devices_bp.route("/devices", methods=["GET", "POST"])
def manage_devices():
    if request.method == "POST":
        """
        Robust Device Registration
        Prioritizes updating existing HWID records to avoid foreign key conflicts.
        """
        data = request.get_json()
        sid = data.get("system_id")
        hid = data.get("hardware_id")
        pc_name = data.get("pc_name")
        city = data.get("city")
        tehsil = data.get("tehsil")
        lab_name = data.get("lab_name")

        if not sid or not hid:
            return jsonify({"error": "System ID and Hardware ID are required"}), 400

        try:
            # 1. Search by HWID - This is the machine's physical identity
            hwid_check = extensions.supabase.table("devices").select("*").eq("hardware_id", hid).execute()
            
            payload = {
                "pc_name": pc_name,
                "city": city,
                "tehsil": tehsil,
                "lab_name": lab_name,
                "status": "offline",
                "last_seen": None
            }

            if hwid_check.data:
                # MACHINE ALREADY EXISTS. Update the existing record.
                target_sid = hwid_check.data[0]["system_id"]
                logger.info(f"Updating Machine: {hid} | Current ID: {target_sid} -> Requested ID: {sid}")
                
                # If the user requested a NEW System ID for this machine, update the ID too!
                # This ensures the Dashboard sees the 'new' PC row as Online.
                if sid and sid != target_sid:
                    logger.info(f"🔄 RE-ASSIGNING System ID for {hid} from {target_sid} to {sid}")
                    res = extensions.supabase.table("devices").update({**payload, "system_id": sid, "hardware_id": hid}).eq("system_id", target_sid).execute()
                else:
                    res = extensions.supabase.table("devices").update(payload).eq("system_id", target_sid).execute()
            else:
                # NEW MACHINE. Check if System ID matches someone else.
                sid_check = extensions.supabase.table("devices").select("*").eq("system_id", sid).execute()
                if sid_check.data:
                    # System ID exists but with different HWID. Binding current machine to this logic ID.
                    logger.info(f"Binding new HWID {hid} to existing System ID {sid}")
                    res = extensions.supabase.table("devices").update({**payload, "hardware_id": hid}).eq("system_id", sid).execute()
                else:
                    # Pure new record
                    logger.info(f"Registering brand new Machine: {pc_name} (ID: {sid})")
                    res = extensions.supabase.table("devices").insert({**payload, "system_id": sid, "hardware_id": hid}).execute()

            return jsonify({"status": "success", "device": res.data[0] if res.data else None})
        except Exception as e:
            logger.error(f"Registration Error: {e}")
            return jsonify({"error": str(e)}), 500

    # GET LOGIC (Previously get_devices)
    city_filter = request.args.get("city")
    lab_filter = request.args.get("lab")
    status_filter = request.args.get("status")
    search = request.args.get("search")

    try:
        now = datetime.now(timezone.utc)
        # Cleanup stale
        stale_threshold_db = now - timedelta(seconds=60)
        try:
            extensions.supabase.table("devices").update({"status": "offline"}).eq("status", "online").lt("last_seen", stale_threshold_db.isoformat()).execute()
        except: pass

        query = extensions.supabase.table("devices").select("*")
        if city_filter:
            query = query.ilike("city", city_filter)
        
        res = query.limit(5000).execute()
        raw_rows = res.data if res.data else []
        
        import re
        def normalize(name):
            if not name: return ""
            return re.sub(r'[^a-z0-9]', '', str(name).lower())

        target_city = normalize(city_filter) if city_filter else None
        target_lab = normalize(lab_filter) if lab_filter else None
        target_search = normalize(search) if search else None

        total_found = []
        for d in raw_rows:
            if target_city and normalize(d.get("city") or "Unknown") != target_city: continue
            if target_lab and normalize(d.get("lab_name") or "Main Lab") != target_lab: continue
            if target_search and target_search not in normalize(d.get("pc_name") or ""): continue
            total_found.append(d)
        
        threshold = now - timedelta(seconds=60)
        processed_devices = []
        for d in total_found:
            last_seen_val = d.get("last_seen")
            is_truly_online = False
            if d.get("status") == "online" and last_seen_val:
                try:
                    ls_dt = datetime.fromisoformat(last_seen_val.replace('Z', '+00:00'))
                    if ls_dt > threshold: is_truly_online = True
                except: pass
            
            if status_filter == 'online' and not is_truly_online: continue
            if status_filter == 'offline' and is_truly_online: continue
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
            "lab_name": data.get("lab_name"),
            "tehsil": data.get("tehsil")
        }).eq("system_id", hid).execute()
        
        return jsonify({"status": "updated", "device": res.data[0]})
    except Exception as e:
        logger.error(f"Error updating device: {e}")
        return jsonify({"error": str(e)}), 500


