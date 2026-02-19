from flask import Blueprint, request, jsonify
from datetime import datetime
import app.extensions as extensions
from app.utils.logger import logger
import threading
import time
from datetime import timedelta

agent_bp = Blueprint("agent", __name__)

def process_app_logs_background(sys_id, date_str, usage_map):
    """Professional Batch Upsert for Granular Logs (Runs Offline/Async)"""
    try:
        if not usage_map: return
        
        log_entries = []
        for app, sec in usage_map.items():
            try:
                # Force cast to integer to prevent "invalid input syntax for type integer"
                clean_sec = int(float(sec))
            except:
                clean_sec = 0

            log_entries.append({
                "device_id": sys_id,
                "date": date_str,
                "app_name": app,
                "seconds_added": clean_sec
            })
        
        if log_entries:
            # PROFESSIONAL: Batch upsert is 100x faster than serial loops
            extensions.supabase.table("app_usage_logs").upsert(
                log_entries, 
                on_conflict="device_id,date,app_name"
            ).execute()
    except Exception as e:
        logger.error(f"⚠️ Async Log Flush Failed: {e}")

@agent_bp.route("/auth", methods=["POST"])
def authenticate_hardware():
    """Verify if a hardware ID is registered in the database."""
    data = request.get_json(force=True)
    hid = data.get("hardware_id")
    
    if not hid:
        return jsonify({"error": "Missing Hardware ID"}), 400
        
    try:
        # Check if agent is providing city/lab info to update DB
        city = data.get("city")
        lab_name = data.get("lab_name")
        college = data.get("college")
        pc_name = data.get("pc_name")
        
        if city or lab_name or college or pc_name:
            update_payload = {}
            if city: update_payload["city"] = city
            if lab_name: update_payload["lab_name"] = lab_name
            if college: update_payload["college"] = college
            if pc_name: update_payload["pc_name"] = pc_name
            
            extensions.supabase.table("devices").update(update_payload).eq("hardware_id", hid).execute()

        res = extensions.supabase.table("devices").select("*").eq("hardware_id", hid).execute()
        if res.data:
            device = res.data[0]
            return jsonify({
                "status": "authorized",
                "system_id": device.get("system_id"),
                "city": device.get("city"),
                "college": device.get("college"),
                "lab_name": device.get("lab_name"),
                "pc_name": device.get("pc_name")
            })
        else:
            return jsonify({
                "status": "unregistered",
                "hardware_id": hid,
                "message": "Security Verification Failed: Hardware ID not found in registry."
            })
    except Exception as e:
        logger.error(f"Auth Error: {e}")
        return jsonify({"error": str(e)}), 500

@agent_bp.route("/heartbeat", methods=["POST"])
def heartbeat():
    data = request.get_json(force=True)
    hid = data.get("hardware_id")
    # Note: pc_name, city, lab_name, cpu_score will be updated ONLY if bound
    
    if not hid:
        return jsonify({"error": "Missing Hardware ID"}), 400

    now_dt = datetime.utcnow()
    now_iso = now_dt.isoformat() + "Z"

    try:
        # 1. Check if this machine is bound to any System ID
        res = extensions.supabase.table("devices").select("*").eq("hardware_id", hid).execute()
        
        if not res.data:
            # Machine is NOT bound. Agent must call /bind first.
            return jsonify({
                "status": "unregistered",
                "message": "This machine is not bound to a System ID. Please register.",
                "hardware_id": hid
            })

        device = res.data[0]
        sys_id = device["system_id"]
        
        # Agent provided times
        agent_start = data.get("session_start")
        agent_active = data.get("last_active") or now_iso
        pc_name = data.get("pc_name")
        cpu_score = data.get("cpu_score", 0)
        city = data.get("city")
        college = data.get("college")
        lab_name = data.get("lab_name")
        # Sanitize numeric data to prevent "invalid input syntax for type integer: '345.4'"
        try:
            runtime_mins = int(float(data.get("runtime_minutes", 0)))
        except:
            runtime_mins = 0

        update_data = {
            "last_seen": now_iso,
            "today_last_active": agent_active,
            "pc_name": pc_name,
            "cpu_score": float(data.get("cpu_score", 0)),
            "runtime_minutes": runtime_mins,
            "status": data.get("status", "online")
        }

        if city: update_data["city"] = city
        if college: update_data["college"] = college
        if lab_name: update_data["lab_name"] = lab_name

        # Sanitize app_usage (durations should be integers)
        incoming_usage = data.get("app_usage", {})
        
        # Filter out background noise and cast to int
        filtered_usage = {}
        for app, sec_val in incoming_usage.items():
            if "python" in app.lower() or "antigravity" in app.lower() or "lab_systems_agent" in app.lower():
                continue
            try:
                filtered_usage[app] = int(float(sec_val))
            except:
                filtered_usage[app] = 0
        
        update_data["app_usage"] = filtered_usage

        last_seen_str = device.get("last_seen")
        stored_start = device.get("today_start_time")

        # TRIGGER ARCHIVE: Only when the calendar day actually rolls over
        if last_seen_str:
            last_seen_dt = datetime.fromisoformat(last_seen_str.replace('Z', '+00:00'))
            
            if now_dt.date() > last_seen_dt.date():
                logger.info(f"📅 Daily Archive for {pc_name} (New Day Detected)")
                try:
                    history_data = {
                        "device_id": sys_id, 
                        "history_date": last_seen_dt.date().isoformat(),
                        "avg_score": device.get("cpu_score", 0),
                        "runtime_minutes": device.get("runtime_minutes", 0),
                        "start_time": device.get("today_start_time") or device.get("last_seen") or now_iso,
                        "end_time": device.get("today_last_active") or device.get("last_seen") or now_iso,
                        "city": device.get("city"),
                        "college": device.get("college"),
                        "lab_name": device.get("lab_name"),
                        "app_usage": device.get("app_usage", {})
                    }
                    # FIX: Use on_conflict to prevent 409 errors
                    extensions.supabase.table("device_daily_history").upsert(history_data, on_conflict="device_id,history_date").execute()
                    
                    # New day starts now
                    update_data["today_start_time"] = agent_start or now_iso
                    # Reset merged usage for the new day
                    update_data["app_usage"] = incoming_usage
                except Exception as e:
                    logger.error(f"Archive Error: {e}")
            else:
                if not device.get("today_start_time"):
                    update_data["today_start_time"] = agent_start or now_iso
        else:
            update_data["today_start_time"] = agent_start or now_iso
        
        # --- SESSION TRACKING ---
        previous_status = device.get("status")
        is_now_online = update_data["status"] == "online"
        
        # 1. Check if we need to start a session (Transition OR First of the day)
        should_start_session = False
        if is_now_online:
            if previous_status == "offline":
                should_start_session = True
            else:
                # Even if already online, check if any session exists for TODAY (UTC)
                try:
                    today_utc = now_dt.date().isoformat()
                    check_session = extensions.supabase.table("device_sessions")\
                        .select("id", count='exact')\
                        .eq("device_id", sys_id)\
                        .gte("start_time", f"{today_utc}T00:00:00Z")\
                        .execute()
                    if check_session.count == 0:
                        should_start_session = True
                except: pass

        if should_start_session:
            try:
                extensions.supabase.table("device_sessions").insert({
                    "device_id": sys_id,
                    "city": city,
                    "college": college,
                    "lab_name": lab_name,
                    "avg_score": cpu_score,
                    "start_time": now_iso
                }).execute()
            except Exception as e:
                logger.error(f"Session Start Error: {e}")

        extensions.supabase.table("devices").update(update_data).eq("system_id", sys_id).execute()


        # ASYNC LOGGING: Move heavy db work to background thread
        if incoming_usage:
            threading.Thread(
                target=process_app_logs_background, 
                args=(sys_id, now_dt.date().isoformat(), incoming_usage), 
                daemon=True
            ).start()

        return jsonify({"status": "ok", "system_id": sys_id, "server_time": now_iso})


    except Exception as e:
        logger.error(f"Fatal in Heartbeat: {e}")
        return jsonify({"error": str(e)}), 500

@agent_bp.route("/available-systems", methods=["GET"])
def get_available_systems():
    try:
        # List systems that haven't been bound yet (hardware_id is null)
        res = extensions.supabase.table("devices").select("system_id, city, college, lab_name").is_("hardware_id", "null").execute()
        return jsonify(res.data)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@agent_bp.route("/sync-offline-data", methods=["POST"])
def sync_offline_data():
    """
    Professional Offline Sync:
    Accepts historical data from agents that were offline.
    Upserts into device_daily_history for the specific date.
    """
    data = request.get_json(force=True)
    sys_id = data.get("system_id")
    date_str = data.get("date") # YYYY-MM-DD
    
    if not sys_id or not date_str:
        return jsonify({"error": "Missing system_id or date"}), 400
        
    try:
        # Prepare historical record
        # ENSURE TYPES: runtime_minutes must be an integer for DB integrity
        try:
            runtime_raw = data.get("runtime_minutes", 0)
            runtime_mins = int(float(runtime_raw)) # Handle string "38.0" or float 38.0
        except:
            runtime_mins = 0

        history_record = {
            "device_id": sys_id,
            "history_date": date_str,
            "avg_score": data.get("cpu_score", 0),
            "runtime_minutes": runtime_mins,
            "start_time": data.get("start_time"),
            "end_time": data.get("end_time"),
            "city": data.get("city"),
            "college": data.get("college"),
            "lab_name": data.get("lab_name"),
            "app_usage": data.get("app_usage", {})
        }
        
        incoming_usage = data.get("app_usage", {})
        
        # Merging Logic: Check if row already exists and merge app_usage
        check_res = extensions.supabase.table("device_daily_history") \
            .select("app_usage, runtime_minutes, avg_score") \
            .eq("device_id", sys_id) \
            .eq("history_date", date_str) \
            .execute()
        
        if check_res.data:
            existing = check_res.data[0]
            existing_usage = existing.get("app_usage") or {}
            
            # Merge usage maps
            for app, sec in incoming_usage.items():
                existing_usage[app] = existing_usage.get(app, 0) + sec
            
            history_record["app_usage"] = existing_usage
            # AUTHORITATIVE: Use the higher value, don't SUM (prevents geometric growth)
            history_record["runtime_minutes"] = max(int(existing.get("runtime_minutes", 0)), runtime_mins)
            # Simple average for score
            history_record["avg_score"] = (float(existing.get("avg_score", 0)) + float(data.get("cpu_score", 0))) / 2
            
            # Merge Times: Keep earliest start and latest end
            if existing.get("start_time") and history_record.get("start_time"):
                history_record["start_time"] = min(existing["start_time"], history_record["start_time"])
            elif existing.get("start_time"):
                history_record["start_time"] = existing["start_time"]

            if existing.get("end_time") and history_record.get("end_time"):
                history_record["end_time"] = max(existing["end_time"], history_record["end_time"])
            elif existing.get("end_time"):
                history_record["end_time"] = existing["end_time"]

        # Upsert: If data for this day already exists, we update it
        extensions.supabase.table("device_daily_history").upsert(history_record, on_conflict="device_id,history_date").execute()
        
        # Background Log Sync (Batch)
        if incoming_usage:
            threading.Thread(target=process_app_logs_background, args=(sys_id, date_str, incoming_usage), daemon=True).start()

        logger.info(f"💾 Offline Sync Successful (Merged) for {sys_id} on {date_str}")
        return jsonify({"status": "synced", "merged": True, "date": date_str})
    except Exception as e:
        logger.error(f"Offline Sync Error: {e}")
        return jsonify({"error": str(e)}), 500

@agent_bp.route("/bind", methods=["POST"])
def bind_system():
    data = request.get_json(force=True)
    hid = data.get("hardware_id")
    sys_id = data.get("system_id")
    
    if not hid or not sys_id:
        return jsonify({"error": "Missing hardware_id or system_id"}), 400
        
    try:
        # 1. Verify sys_id is available
        check = extensions.supabase.table("devices").select("hardware_id").eq("system_id", sys_id).execute()
        if not check.data:
            return jsonify({"error": "System ID not found"}), 404
        if check.data[0]["hardware_id"] is not None:
            return jsonify({"error": "System ID already bound"}), 400
            
        # 2. Get pre-defined info
        res = extensions.supabase.table("devices").select("city, college, lab_name").eq("system_id", sys_id).execute()
        device_info = res.data[0] if res.data else {}
        
        # 3. Bind it
        extensions.supabase.table("devices").update({"hardware_id": hid}).eq("system_id", sys_id).execute()
        logger.info(f"🔗 Bound Machine {hid} to {sys_id}")
        return jsonify({
            "status": "success", 
            "system_id": sys_id, 
            "city": device_info.get("city"), 
            "college": device_info.get("college"),
            "lab_name": device_info.get("lab_name")
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

def monitor_tasks():
    logger.info("📡 Background Monitor Thread Started.")
    while True:
        try:
            now = datetime.utcnow()
            
            # 1. Mark offline if not seen for > 60 seconds
            threshold = now - timedelta(seconds=60)
            threshold_iso = threshold.isoformat() + "Z"
            
            # Update devices where last_seen < threshold and status is online
            extensions.supabase.table("devices")\
                .update({"status": "offline"})\
                .eq("status", "online")\
                .lt("last_seen", threshold_iso)\
                .execute()

            # 2. Daily Cleanup: Delete heartbeats/sessions older than 24h
            # This keeps DB light while preserving Summaries in History
            cleanup_limit = now - timedelta(days=1)
            cleanup_iso = cleanup_limit.isoformat() + "Z"
            cleanup_date = cleanup_limit.date().isoformat()

            # A. Delete transient sessions (24h)
            extensions.supabase.table("device_sessions")\
                .delete()\
                .lt("start_time", cleanup_iso)\
                .execute()
            
            # B. Delete granular app usage (24h - already archived to history at midnight)
            extensions.supabase.table("app_usage_logs")\
                .delete()\
                .lt("date", cleanup_date)\
                .execute()

            # 3. History Cleanup: Removed as per USER request (Keep data permanently)
            # history_keep_limit = (now - timedelta(days=6)).date().isoformat()
            # extensions.supabase.table("device_daily_history")\
            #     .delete()\
            #     .lt("history_date", history_keep_limit)\
            #     .execute()

        except Exception as e:
            logger.error(f"Error in Background Tasks: {e}")
        
        time.sleep(60) # Run check every 60 seconds

# Start the background tasks thread
monitor_thread = threading.Thread(target=monitor_tasks, daemon=True)
monitor_thread.start()
