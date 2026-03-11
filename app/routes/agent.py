from flask import Blueprint, request, jsonify
from datetime import datetime
import app.extensions as extensions
from app.utils.logger import logger
import threading
import time
from datetime import timedelta
import os
import subprocess
import sys

agent_bp = Blueprint("agent", __name__)

# Global caches for Zero-Touch Deployment
discovery_cache = {} # {hwid: {pc_name, last_seen}}
trigger_cache = {}   # {hwid: action}

@agent_bp.route("/discovery/pending", methods=["GET"])
def get_pending_discovery():
    """Lists unregistered devices that have beat their heart recently"""
    now = datetime.utcnow()
    # Cleanup items older than 3 minutes
    for hid in list(discovery_cache.keys()):
        ls = datetime.fromisoformat(discovery_cache[hid]["last_seen"].replace('Z', '+00:00'))
        if (now - ls.replace(tzinfo=None)).total_seconds() > 180:
            del discovery_cache[hid]
    return jsonify(discovery_cache)

@agent_bp.route("/trigger", methods=["POST"])
def trigger_action():
    """Admin endpoint to queue actions for specific hardware"""
    data = request.get_json(force=True)
    hid = data.get("hardware_id")
    action = data.get("action")
    if not hid or not action:
        return jsonify({"error": "Missing hardware_id or action"}), 400
    
    trigger_cache[hid] = action
    return jsonify({"status": "queued", "action": action, "target": hid})

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
        # Hierarchy and identity should be managed via Dashboard, 
        # but allowed here for agent-side manual syncs.
        city = data.get("city")
        lab_name = data.get("lab_name")
        tehsil = data.get("tehsil")
        pc_name = data.get("pc_name")

        if city or lab_name or tehsil or pc_name:
            update_payload = {}
            if city: update_payload["city"] = city
            if lab_name: update_payload["lab_name"] = lab_name
            if tehsil: update_payload["tehsil"] = tehsil
            if pc_name: update_payload["pc_name"] = pc_name
            
            extensions.supabase.table("devices").update(update_payload).eq("hardware_id", hid).execute()

        res = extensions.supabase.table("devices").select("*").eq("hardware_id", hid).execute()
        if res.data:
            device = res.data[0]
            return jsonify({
                "status": "authorized",
                "system_id": device.get("system_id"),
                "city": device.get("city"),
                "tehsil": device.get("tehsil"),
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
            # --- DISCOVERY LOGIC ---
            logger.warning(f"🕵️ DISCOVERY: Unregistered Heartbeat from {hid} (PC: {data.get('pc_name')})")
            
            # Save this unknown device to cache so Dashboard can find it
            discovery_cache[hid] = {
                "pc_name": data.get("pc_name") or f"Unknown-{hid[:8]}",
                "last_seen": now_iso
            }
            
            # Machine is NOT bound. Agent must call /bind first.
            return jsonify({
                "status": "unregistered",
                "message": "Discovery broadcast active. Link your PC in the Dashboard.",
                "hardware_id": hid,
                # Include local trigger check even for unregistered
                "remote_action": trigger_cache.pop(hid, None)
            })

        # Machine is bound, remove from discovery
        discovery_cache.pop(hid, None)

        device = res.data[0]
        sys_id = device["system_id"]
        
        # Agent provided times
        agent_start = data.get("session_start")
        agent_active = data.get("last_active") or now_iso
        # pc_name = data.get("pc_name") # AUTHORITATIVE: Dashboard/DB manages PC Friendly Names
        cpu_score = data.get("cpu_score", 0)
        city = data.get("city")
        tehsil = data.get("tehsil")
        lab_name = data.get("lab_name")
        # Sanitize numeric data to prevent "invalid input syntax for type integer: '345.4'"
        try:
            runtime_mins = int(float(data.get("runtime_minutes", 0)))
        except:
            runtime_mins = 0

        update_data = {
            "last_seen": now_iso,
            "today_last_active": agent_active,
            # "pc_name": pc_name, # Prevent Agent from overwriting Dashboard-defined names
            "cpu_score": float(data.get("cpu_score", 0)),
            "runtime_minutes": runtime_mins,
            "status": data.get("status", "online")
        }

        # Hierarchy is now authoritative from DB only.
        if city: update_data["city"] = city
        if tehsil: update_data["tehsil"] = tehsil
        if lab_name: update_data["lab_name"] = lab_name

        # Sanitize app_usage (durations should be integers)
        incoming_usage = data.get("app_usage", {})
        
        # Filter out background noise and cast to int, but PRESERVE special telemetry keys
        filtered_usage = {}
        for app, val in incoming_usage.items():
            if any(noise in app.lower() for noise in ["python", "antigravity", "lab_systems_agent"]):
                continue
            
            try:
                if app == "__current_cpu__":
                    filtered_usage[app] = round(float(val), 1) # Keep precision for load
                else:
                    filtered_usage[app] = int(float(val)) # Standard usage in seconds
            except:
                filtered_usage[app] = 0
        
        update_data["app_usage"] = filtered_usage

        last_seen_str = device.get("last_seen")
        stored_start = device.get("today_start_time")

        # TRIGGER ARCHIVE: Only when the calendar day actually rolls over
        if last_seen_str:
            last_seen_dt = datetime.fromisoformat(last_seen_str.replace('Z', '+00:00'))
            
            if now_dt.date() > last_seen_dt.date():
                logger.info(f"📅 Daily Archive for {device.get('pc_name', 'Unknown PC')} (New Day Detected)")
                try:
                    history_data = {
                        "device_id": sys_id, 
                        "history_date": last_seen_dt.date().isoformat(),
                        "avg_score": device.get("cpu_score", 0),
                        "runtime_minutes": device.get("runtime_minutes", 0),
                        "start_time": device.get("today_start_time") or device.get("last_seen") or now_iso,
                        "end_time": device.get("today_last_active") or device.get("last_seen") or now_iso,
                        "city": device.get("city"),
                        "tehsil": device.get("tehsil"),
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
                    "tehsil": tehsil,
                    "lab_name": lab_name,
                    "avg_score": cpu_score,
                    "start_time": now_iso
                }).execute()
            except Exception as e:
                logger.error(f"Session Start Error: {e}")

        logger.info(f"DEBUG: Heartbeat Update Payload for {sys_id}: {update_data}")
        extensions.supabase.table("devices").update(update_data).eq("system_id", sys_id).execute()
        
        # Broadcast real-time update to dashboard
        try:
            extensions.socketio.emit('device_update', {
                'system_id': sys_id,
                'status': update_data['status'],
                'cpu_score': update_data['cpu_score'],
                'runtime_minutes': update_data['runtime_minutes'],
                'app_usage': filtered_usage
            })
        except: pass


        # ASYNC LOGGING: Move heavy db work to background thread
        if incoming_usage:
            threading.Thread(
                target=process_app_logs_background, 
                args=(sys_id, now_dt.date().isoformat(), incoming_usage), 
                daemon=True
            ).start()

        return jsonify({
            "status": "ok", 
            "system_id": sys_id, 
            "city": device.get("city"),
            "tehsil": device.get("tehsil"),
            "lab_name": device.get("lab_name"),
            "server_time": now_iso,
            "remote_action": trigger_cache.pop(hid, None) # "start", "stop", "install", etc.
        })


    except Exception as e:
        logger.error(f"Fatal in Heartbeat: {e}")
        return jsonify({"error": str(e)}), 500

@agent_bp.route("/available-systems", methods=["GET"])
def get_available_systems():
    try:
        # List systems that haven't been bound yet (hardware_id is null)
        res = extensions.supabase.table("devices").select("system_id, city, tehsil, lab_name").is_("hardware_id", "null").execute()
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
            "tehsil": data.get("tehsil"),
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

@agent_bp.route("/deploy", methods=["POST"])
def deploy_agent():
    """
    Local Automation Endpoint: Allows the Dashboard to trigger 
    agent installation/startup on the server machine.
    """
    data = request.get_json(force=True)
    action = data.get("action") # 'install', 'uninstall', 'start', 'stop'
    
    # 🔍 Auto-Detect Agent Path
    # Case 1: Running from Server folder
    base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
    agent_path = os.path.join(base_dir, "Agent", "System_Monitoring_Agent.py")
    
    if not os.path.exists(agent_path):
        # Fallback for different structures
        agent_path = os.path.abspath(os.path.join(os.getcwd(), "Agent", "System_Monitoring_Agent.py"))
    
    if not os.path.exists(agent_path):
        logger.error(f"Deployment Error: Agent script not found at {agent_path}")
        return jsonify({"error": f"Agent script not found at {agent_path}"}), 404

    python_exe = sys.executable
    
    cmd = []
    if action == "install":
        cmd = [python_exe, agent_path, "--install"]
    elif action == "uninstall":
        cmd = [python_exe, agent_path, "--uninstall"]
    elif action == "start":
        # Launching with --debug or just plain script to show GUI
        cmd = [python_exe, agent_path, "--debug"]
    elif action == "stop":
        # Professional Stop via SC command
        cmd = ["sc", "stop", "SystemMonitoringAgentService"]
    else:
        return jsonify({"error": "Invalid action"}), 400

    try:
        # Launch process. We use Popen so it doesn't block the Flask server.
        # For 'start', we want it to persist. 
        # For 'install', we need it to finish (or at least start).
        logger.info(f"🚀 EXECUTING DEPLOYMENT: {' '.join(cmd)}")
        
        # Windows specific: Use creationflags to run as background/detached if needed
        # but for GUI we want it to show up.
        process = subprocess.Popen(
            cmd, 
            stdout=subprocess.PIPE, 
            stderr=subprocess.PIPE,
            creationflags=subprocess.CREATE_NEW_CONSOLE if action == "start" else 0x08000000 # CREATE_NO_WINDOW
        )
        
        # If it's a quick command like install/uninstall, we can wait a bit to check success
        if action in ["install", "uninstall"]:
            # Give it 2 seconds to fail early
            try:
                stdout, stderr = process.communicate(timeout=3)
                if process.returncode != 0:
                    err_msg = stderr.decode() if stderr else "Unknown error"
                    logger.error(f"Deployment Failed: {err_msg}")
                    return jsonify({"status": "failed", "error": err_msg})
            except subprocess.TimeoutExpired:
                # Still running, probably good (installer might be talking to system)
                pass

        return jsonify({
            "status": "triggered", 
            "action": action, 
            "message": f"Deployment command {action} initiated successfully."
        })
    except Exception as e:
        logger.error(f"Deployment Fatal Error: {e}")
        return jsonify({"error": str(e)}), 500

@agent_bp.route("/local-hwid", methods=["GET"])
def get_local_hwid():
    """Returns the HWID of the current server machine (useful for local setup)"""
    try:
        from .agent import ConfigManager # No, wait, it's not in a separate file yet
        # Actually I'll just use the logic from System_Monitoring_Agent
        import uuid
        import socket
        try:
             import wmi
             c = wmi.WMI()
             for system in c.Win32_ComputerSystemProduct():
                 uuid_val = system.UUID
                 if uuid_val and len(uuid_val) > 10:
                     return jsonify({"hardware_id": uuid_val})
        except: pass
        hwid = str(uuid.uuid5(uuid.NAMESPACE_DNS, socket.gethostname() + str(uuid.getnode())))
        return jsonify({"hardware_id": hwid})
    except Exception as e:
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
        res = extensions.supabase.table("devices").select("city, tehsil, lab_name").eq("system_id", sys_id).execute()
        device_info = res.data[0] if res.data else {}
        
        # 3. Bind it
        extensions.supabase.table("devices").update({"hardware_id": hid}).eq("system_id", sys_id).execute()
        logger.info(f"🔗 Bound Machine {hid} to {sys_id}")
        return jsonify({
            "status": "success", 
            "system_id": sys_id, 
            "city": device_info.get("city"), 
            "tehsil": device_info.get("tehsil"), 
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
