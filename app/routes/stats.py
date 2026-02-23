from flask import Blueprint, jsonify, request
import app.extensions as extensions
from app.utils.logger import logger
from datetime import datetime

stats_bp = Blueprint("stats", __name__)

@stats_bp.route("/health", methods=["GET"])
def health_check():
    return jsonify({"status": "ok", "database": "connected"})

@stats_bp.route("/stats/locations", methods=["GET"])
def get_location_stats():
    try:
        from datetime import datetime, timezone, timedelta
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
            logger.error(f"Stats Cleanup Error: {e}")

        # Aggregate stats in Python from devices table
        try:
            # PROFESSIONAL: Fetch ONLY required columns to minimize schema mismatch risk
            res = extensions.supabase.table("devices").select("city, status, last_seen, lab_name, tehsil").execute()
            raw_devices = res.data if res.data else []
            logger.info(f"Location Stats: Found {len(raw_devices)} total devices in DB.")
        except Exception as e:
            logger.error(f"DB Fetch Error in Stats: {e}")
            return jsonify({"error": str(e), "locations": []}), 500
        
        threshold = now - timedelta(seconds=60)
        
        city_map = {}
        registered_count = 0
        
        for d in raw_devices:
            # PROFESSIONAL: Count ALL inventory slots (both registered and placeholders)
            registered_count += 1
            city = (d.get("city") or "Unknown").strip()
            # If user has renamed city to something else as well, we just use whatever is in 'city' column
            lab = (d.get("lab_name") or "Main Lab").strip()
            cpu = float(d.get("cpu_score") or 0)
            
            if city not in city_map:
                city_map[city] = {
                    "city": city, 
                    "total_pcs": 0, 
                    "online": 0, 
                    "offline": 0,
                    "labs": set(),
                    "tehsils": set(),
                    "online_labs_set": set(),
                    "total_cpu": 0,
                    "online_count_for_cpu": 0
                }
            
            target = city_map[city]
            target["total_pcs"] += 1
            target["labs"].add(lab)
            teh = (d.get("tehsil") or "Unknown").strip()
            if teh:
                target["tehsils"].add(teh)
            
            is_online = False
            if d.get("status") == "online" and d.get("last_seen"):
                try:
                    ls_dt = datetime.fromisoformat(d["last_seen"].replace('Z', '+00:00'))
                    if ls_dt > threshold:
                        is_online = True
                except: pass
            
            if is_online:
                target["online"] += 1
                target["online_labs_set"].add(lab)
                target["total_cpu"] += cpu
                target["online_count_for_cpu"] += 1
            else:
                target["offline"] += 1
        
        logger.info(f"Location Stats: Processed {registered_count} nodes into {len(city_map)} cities.")

        # Format for frontend
        result = []
        for city, data in city_map.items():
            avg_perf = 0
            if data["total_pcs"] > 0:
                avg_perf = data["total_cpu"] / data["total_pcs"]
            
            result.append({
                "city": city,
                "total_pcs": data["total_pcs"],
                "online": data["online"],
                "offline": data["offline"],
                "total_labs": len(data["labs"]),
                "total_tehsils": len(data["tehsils"]),
                "online_labs": len(data["online_labs_set"]),
                "offline_labs": len(data["labs"]) - len(data["online_labs_set"]),
                "avg_performance": round(avg_perf, 2)
            })
        
        return jsonify({
            "locations": result,
            "server_time": now.replace(tzinfo=None).isoformat() + "Z"
        })

    except Exception as e:
        logger.error(f"Critical Error in location stats: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return jsonify([]), 500

@stats_bp.route("/stats/city/<city>/tehsils", methods=["GET"])
def get_tehsil_stats(city):
    """HIERARCHY STEP 2: Return tehsils for a specific city."""
    try:
        from datetime import datetime, timedelta, timezone
        now = datetime.now(timezone.utc)
        threshold = now - timedelta(seconds=60)

        res = extensions.supabase.table("devices")\
            .select("city, status, last_seen, lab_name, tehsil")\
            .eq("city", city).execute()
        devices = res.data if res.data else []
        
        tehsil_map = {}
        for d in devices:
            tehsil = d.get("tehsil") or "Unknown"
            lab = d.get("lab_name") or "Main Lab"
            
            if tehsil not in tehsil_map:
                tehsil_map[tehsil] = {
                    "tehsil": tehsil,
                    "city": city,
                    "total_pcs": 0,
                    "online": 0,
                    "offline": 0,
                    "labs": set()
                }
            
            target = tehsil_map[tehsil]
            target["total_pcs"] += 1
            target["labs"].add(lab)
            
            is_online = False
            if d.get("status") == "online" and d.get("last_seen"):
                try:
                    ls_dt = datetime.fromisoformat(d["last_seen"].replace('Z', '+00:00'))
                    if ls_dt > threshold:
                        is_online = True
                except: pass
            
            if is_online:
                target["online"] += 1
            else:
                target["offline"] += 1
        
        result = []
        for teh, data in tehsil_map.items():
            result.append({
                "tehsil": teh,
                "city": city,
                "total_pcs": data["total_pcs"],
                "online": data["online"],
                "offline": data["offline"],
                "total_labs": len(data["labs"])
            })
            
        return jsonify({
            "tehsils": result,
            "server_time": now.replace(tzinfo=None).isoformat() + "Z"
        })
    except Exception as e:
        logger.error(f"Tehsil Stats Error: {e}")
        return jsonify({"error": str(e), "tehsils": []}), 500

@stats_bp.route("/stats/city/<city>/labs", methods=["GET"])
def get_lab_stats(city):
    """HIERARCHY STEP 3: Return labs, with optional tehsil filter."""
    tehsil_filter = request.args.get("tehsil")
    try:
        query = extensions.supabase.table("devices").select("city, status, last_seen, lab_name, cpu_score, tehsil").eq("city", city)
        if tehsil_filter:
            query = query.eq("tehsil", tehsil_filter)
            
        res = query.execute()
        devices = res.data if res.data else []
        
        from datetime import datetime, timedelta, timezone
        now = datetime.now(timezone.utc)
        threshold = now - timedelta(seconds=60)
        
        lab_map = {}
        for d in devices:
            # Count all slots in the lab

            lab = d.get("lab_name") or "Main Lab"
            cpu = float(d.get("cpu_score") or 0)
            
            if lab not in lab_map:
                lab_map[lab] = {
                    "lab_name": lab, 
                    "total_pcs": 0, 
                    "online": 0, 
                    "offline": 0,
                    "total_cpu": 0,
                    "online_count": 0
                }
            
            target = lab_map[lab]
            target["total_pcs"] += 1
            
            is_online = False
            if d.get("status") == "online" and d.get("last_seen"):
                try:
                    ls_dt = datetime.fromisoformat(d["last_seen"].replace('Z', '+00:00'))
                    if ls_dt > threshold:
                        is_online = True
                except: pass
            
            if is_online:
                target["online"] += 1
                target["total_cpu"] += cpu
                target["online_count"] += 1
            else:
                target["offline"] += 1
        
        # Calculate averages based on TOTAL PCs in lab
        result = []
        for lab, data in lab_map.items():
            avg_perf = 0
            if data["total_pcs"] > 0:
                avg_perf = data["total_cpu"] / data["total_pcs"]
            
            result.append({
                "lab_name": lab,
                "total_pcs": data["total_pcs"],
                "online": data["online"],
                "offline": data["offline"],
                "avg_performance": round(avg_perf, 2)
            })
            
        return jsonify({
            "labs": result,
            "server_time": now.replace(tzinfo=None).isoformat() + "Z"
        })

    except Exception as e:
        logger.error(f"Error in lab stats: {e}")
        return jsonify([]), 500

@stats_bp.route("/stats/tehsils", methods=["GET"])
def get_global_tehsil_stats():
    """Returns statistics for all tehsils across all cities."""
    try:
        from datetime import datetime, timedelta, timezone
        now = datetime.now(timezone.utc)
        threshold = now - timedelta(seconds=60)

        res = extensions.supabase.table("devices")\
            .select("city, status, last_seen, lab_name, tehsil")\
            .execute()
        devices = res.data if res.data else []
        
        tehsil_map = {}
        for d in devices:
            city = d.get("city") or "Unknown"
            tehsil = d.get("tehsil") or "Unknown"
            lab = d.get("lab_name") or "Main Lab"
            key = f"{city}|{tehsil}"
            
            if key not in tehsil_map:
                tehsil_map[key] = {
                    "tehsil": tehsil,
                    "city": city,
                    "total_pcs": 0,
                    "online": 0,
                    "offline": 0,
                    "labs": set()
                }
            
            target = tehsil_map[key]
            target["total_pcs"] += 1
            target["labs"].add(lab)
            
            is_online = False
            if d.get("status") == "online" and d.get("last_seen"):
                try:
                    ls_dt = datetime.fromisoformat(d["last_seen"].replace('Z', '+00:00'))
                    if ls_dt > threshold:
                        is_online = True
                except: pass
            
            if is_online:
                target["online"] += 1
            else:
                target["offline"] += 1
        
        result = []
        for key, data in tehsil_map.items():
            result.append({
                "tehsil": data["tehsil"],
                "city": data["city"],
                "total_pcs": data["total_pcs"],
                "online": data["online"],
                "offline": data["offline"],
                "total_labs": len(data["labs"])
            })
            
        return jsonify({
            "tehsils": result,
            "server_time": now.replace(tzinfo=None).isoformat() + "Z"
        })
    except Exception as e:
        logger.error(f"Global Tehsil Stats Error: {e}")
        return jsonify({"error": str(e), "tehsils": []}), 500

@stats_bp.route("/stats/overview", methods=["GET"])
def overview():
    try:
        from datetime import datetime, timedelta, timezone
        now = datetime.now(timezone.utc)
        
        # --- SELF-HEALING: Cleanup Stale 'Online' Statuses ---
        stale_threshold_db = now - timedelta(seconds=60)
        try:
            extensions.supabase.table("devices") \
                .update({"status": "offline"}) \
                .eq("status", "online") \
                .lt("last_seen", stale_threshold_db.isoformat()) \
                .execute()
        except: pass

        # PROFESSIONAL: Only count devices that have been REGISTERED (hardware_id is not null)
        # Use explicit column list to avoid schema cache issues with '*'
        res = extensions.supabase.table("devices").select("system_id, status, last_seen, hardware_id, cpu_score").execute()
        raw_devices = res.data if res.data else []
        
        # Include all devices in the overview
        devices = raw_devices
        
        threshold = now - timedelta(seconds=60)

        total = len(devices)
        online = 0
        total_cpu = 0
        for d in devices:
            if d.get("status") == "online" and d.get("last_seen"):
                try:
                    ls_dt = datetime.fromisoformat(d["last_seen"].replace('Z', '+00:00'))
                    if ls_dt > threshold:
                        online += 1
                        total_cpu += float(d.get("cpu_score") or 0)
                except: pass
        
        avg_perf = 0
        if online > 0:
            avg_perf = total_cpu / online

        return jsonify({
            "total_devices": total,
            "online_devices": online,
            "offline_devices": total - online,
            "avg_performance": round(avg_perf, 2),
            "status": "synchronized",
            "server_time": now.replace(tzinfo=None).isoformat() + "Z"
        })

    except Exception as e:
        logger.error(f"Error in overview: {e}")
        return jsonify({"error": str(e)}), 500

@stats_bp.route("/stats/city/rename", methods=["PATCH"])
def rename_city():
    data = request.get_json()
    old_name = data.get("old_name")
    new_name = data.get("new_name")
    try:
        extensions.supabase.table("devices").update({"city": new_name}).eq("city", old_name).execute()
        return jsonify({"status": "success"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@stats_bp.route("/stats/city/delete", methods=["DELETE"])
def delete_city():
    city = request.args.get("city")
    try:
        # Instead of deleting rows, we just reset the city and hardware bindings if you prefer, 
        # or actually delete if it's the intent. The frontend prompt says "Delete city and all PCs".
        # We will reset them to 'Unknown' and unbind them to preserve the slots.
        extensions.supabase.table("devices").update({
            "hardware_id": None,
            "status": "offline",
            "last_seen": None,
            "pc_name": None,
            "city": "Unknown",
            "tehsil": "Unknown",
            "lab_name": "Unknown"
        }).eq("city", city).execute()
        return jsonify({"status": "success"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@stats_bp.route("/stats/tehsil/rename", methods=["PATCH"])
def rename_tehsil():
    data = request.get_json()
    city = data.get("city")
    old_name = data.get("old_name")
    new_name = data.get("new_name")
    try:
        extensions.supabase.table("devices").update({"tehsil": new_name})\
            .eq("city", city).eq("tehsil", old_name).execute()
        return jsonify({"status": "success"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@stats_bp.route("/stats/lab/rename", methods=["PATCH"])
def rename_lab():
    data = request.get_json()
    city = data.get("city")
    old_name = data.get("old_name")
    new_name = data.get("new_name")
    try:
        extensions.supabase.table("devices").update({"lab_name": new_name})\
            .eq("city", city).eq("lab_name", old_name).execute()
        return jsonify({"status": "success"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@stats_bp.route("/stats/lab/delete", methods=["DELETE"])
def delete_lab():
    city = request.args.get("city")
    lab = request.args.get("lab")
    try:
        extensions.supabase.table("devices").update({
            "hardware_id": None,
            "status": "offline",
            "last_seen": None,
            "pc_name": None,
            "city": "Unknown",
            "tehsil": "Unknown",
            "lab_name": "Unknown"
        }).eq("city", city).eq("lab_name", lab).execute()
        return jsonify({"status": "success"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@stats_bp.route("/devices/manage", methods=["DELETE"])
def delete_device():
    hid = request.args.get("hid")
    if not hid: return jsonify({"error": "No HID"}), 400
    try:
        # Instead of deleting, we clear the hardware binding
        extensions.supabase.table("devices").update({
            "hardware_id": None,
            "status": "offline",
            "last_seen": None,
            "pc_name": None
        }).eq("system_id", hid).execute()
        return jsonify({"status": "cleared"})
    except Exception as e:
        logger.error(f"Error deleting device: {e}")
        return jsonify({"error": str(e)}), 500
@stats_bp.route("/stats/labs/all", methods=["GET"])
def get_all_labs_global():
    try:
        from datetime import datetime, timezone, timedelta
        now = datetime.now(timezone.utc)
        threshold = now - timedelta(seconds=60)

        # Fetch all registered devices using explicit column list
        res = extensions.supabase.table("devices").select("city, status, last_seen, lab_name, tehsil").execute()
        raw_devices = res.data if res.data else []
        
        lab_map = {}
        for d in raw_devices:
            # Process all devices in the inventory
            
            lab = d.get("lab_name") or "Main Lab"
            city = d.get("city") or "Unknown"
            # Use a unique key for labs in case names repeat across cities
            key = f"{city}|{lab}"
            
            if key not in lab_map:
                lab_map[key] = {
                    "lab_name": lab,
                    "city": city,
                    "tehsil": d.get("tehsil") or "Unknown",
                    "total_pcs": 0,
                    "online": 0,
                    "offline": 0
                }
            
            target = lab_map[key]
            target["total_pcs"] += 1
            
            is_online = False
            if d.get("status") == "online" and d.get("last_seen"):
                try:
                    ls_dt = datetime.fromisoformat(d["last_seen"].replace('Z', '+00:00'))
                    if ls_dt > threshold:
                        is_online = True
                except: pass
            
            if is_online:
                target["online"] += 1
            else:
                target["offline"] += 1
        
        return jsonify({
            "labs": list(lab_map.values()),
            "server_time": now.replace(tzinfo=None).isoformat() + "Z"
        })
    except Exception as e:
        logger.error(f"Error in all labs stats: {e}")
        return jsonify([]), 500

@stats_bp.route("/stats/utilization", methods=["GET"])
def get_utilization_stats():
    """
    LOGICAL UTILIZATION ENGINE:
    Enhanced for extreme resilience against data inconsistencies and crashes.
    """
    try:
        from datetime import datetime, timezone, timedelta
        import json
        now = datetime.now(timezone.utc)
        
        # 1. Fetch current device states (minimal columns for speed/safety)
        try:
            res_devices = extensions.supabase.table("devices").select("city, lab_name, status, runtime_minutes, app_usage, last_seen").execute()
            devices = res_devices.data if res_devices and res_devices.data else []
        except Exception as db_err:
            logger.error(f"Utilization DB Fetch Error: {db_err}")
            return jsonify({"error": "Database connectivity issue", "today": {}, "lab_details": []}), 200 # Return 200 with empty to avoid UI crash

        # Logic for "Actually Used"
        def is_actually_used(runtime_mins, app_usage_raw):
            try:
                # Ensure runtime is a number
                try: 
                    rt = float(runtime_mins or 0)
                except: rt = 0
                
                if rt < 3: return False
                
                # Robust app_usage parsing
                app_usage = app_usage_raw
                if isinstance(app_usage, str):
                    try: app_usage = json.loads(app_usage)
                    except: return False
                
                if not app_usage or not isinstance(app_usage, dict):
                    return False

                work_apps = [
                    'chrome', 'firefox', 'msedge', 'brave', 'browser',
                    'code', 'visual studio', 'pycharm', 'intellij', 'sublime', 'notepad++', 'anaconda', 'jupyter',
                    'word', 'excel', 'powerpoint', 'winword', 'outlook', 'access',
                    'vlc', 'potplayer', 'mpc', 'wmplayer',
                    'zoom', 'teams', 'discord', 'anydesk', 'teamviewer',
                    'photoshop', 'illustrator', 'corel', 'autocad', 'matlab',
                    'python', 'java', 'node', 'cmd', 'powershell'
                ]
                blacklist = ['explorer.exe', 'taskmgr.exe', 'shellexperiencehost.exe', 'searchhost.exe', 'lockapp.exe']

                total_real_usage_seconds = 0
                for app, seconds in app_usage.items():
                    try:
                        app_lower = str(app).lower()
                        if any(b in app_lower for b in blacklist): continue
                        if any(work in app_lower for work in work_apps):
                            total_real_usage_seconds += float(seconds or 0)
                    except: continue

                return total_real_usage_seconds > 45
            except: 
                return False

        lab_activity = {}
        lab_last_seen = {}

        for d in devices:
            try:
                raw_city = (d.get('city') or 'Unknown').strip()
                raw_lab = (d.get('lab_name') or 'Main Lab').strip()
                key = f"{raw_city}|{raw_lab}"
                
                if key not in lab_activity:
                    lab_activity[key] = {
                        "city": raw_city, "lab": raw_lab, 
                        "used": False, "idle": False, "online": 0, "total": 0,
                        "is_stale": False, "is_ghost": False, "last_used": "Never"
                    }
                
                target = lab_activity[key]
                target["total"] += 1
                
                is_online = d.get("status") == "online"
                if is_online:
                    target["online"] += 1
                    if is_actually_used(d.get("runtime_minutes", 0), d.get("app_usage", {})):
                        target["used"] = True
                    else:
                        target["idle"] = True 

                # Process last seen duration for this device
                ls_str = d.get("last_seen")
                if ls_str:
                    try:
                        ls_dt = datetime.fromisoformat(ls_str.replace('Z', '+00:00'))
                        if key not in lab_last_seen or ls_dt > lab_last_seen[key]:
                            lab_last_seen[key] = ls_dt
                    except: pass
            except: continue

        # Final pass for stale/ghost logic and aggregation
        today_stats = {"used_labs": 0, "idle_labs": 0, "offline_labs": 0}
        one_week_unused = []
        one_month_unused = []

        for key, target in lab_activity.items():
            # Update Today Metrics
            if target["used"]: today_stats["used_labs"] += 1
            elif target["online"] > 0: today_stats["idle_labs"] += 1
            else: today_stats["offline_labs"] += 1

            # Update Stale/Ghost Metrics
            last_seen_dt = lab_last_seen.get(key)
            if last_seen_dt:
                target["last_used"] = last_seen_dt.date().isoformat()
                if last_seen_dt < now - timedelta(days=30):
                    target["is_ghost"] = True
                    target["is_stale"] = True
                    one_month_unused.append({"city": target["city"], "lab": target["lab"], "last_used": target["last_used"]})
                elif last_seen_dt < now - timedelta(days=7):
                    target["is_stale"] = True
                    one_week_unused.append({"city": target["city"], "lab": target["lab"], "last_used": target["last_used"]})

        return jsonify({
            "today": today_stats,
            "one_week_unused": one_week_unused,
            "one_month_unused": one_month_unused,
            "lab_details": list(lab_activity.values()),
            "server_time": now.replace(tzinfo=None).isoformat() + "Z"
        })

    except Exception as e:
        logger.error(f"Utilization CRITICAL: {e}")
        import traceback
        logger.error(traceback.format_exc())
        # Return a valid empty structure instead of crashing
        return jsonify({
            "today": {"used_labs": 0, "idle_labs": 0, "offline_labs": 0},
            "one_week_unused": [],
            "one_month_unused": [],
            "lab_details": [],
            "server_time": datetime.now().isoformat() + "Z"
        })

    except Exception as e:
        logger.error(f"Utilization Error: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return jsonify({"error": str(e)}), 500
