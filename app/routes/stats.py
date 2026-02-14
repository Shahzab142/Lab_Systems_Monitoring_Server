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
            res = extensions.supabase.table("devices").select("city, status, last_seen, lab_name, hardware_id, cpu_score").execute()
            raw_devices = res.data if res.data else []
            logger.info(f"Location Stats: Found {len(raw_devices)} total devices in DB.")
        except Exception as e:
            logger.error(f"DB Fetch Error in Stats: {e}")
            return jsonify([]), 500
        
        threshold = now - timedelta(seconds=60)
        
        city_map = {}
        registered_count = 0
        
        for d in raw_devices:
            # PROFESSIONAL: Count ALL inventory slots (both registered and placeholders)
            registered_count += 1
            city = (d.get("city") or "Unknown").strip()
            lab = (d.get("lab_name") or "Main Lab").strip()
            cpu = float(d.get("cpu_score") or 0)
            
            if city not in city_map:
                city_map[city] = {
                    "city": city, 
                    "total_pcs": 0, 
                    "online": 0, 
                    "offline": 0,
                    "labs": set(),
                    "online_labs_set": set(),
                    "total_cpu": 0,
                    "online_count_for_cpu": 0
                }
            
            target = city_map[city]
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
                target["online_labs_set"].add(lab)
                target["total_cpu"] += cpu
                target["online_count_for_cpu"] += 1
            else:
                target["offline"] += 1
        
        logger.info(f"Location Stats: Processed {registered_count} registered nodes into {len(city_map)} cities.")

        # Format for frontend
        result = []
        for city, data in city_map.items():
            avg_perf = 0
            if data["total_pcs"] > 0:
                # Calculate average across ALL PCs (treating offline as 0%)
                avg_perf = data["total_cpu"] / data["total_pcs"]
            
            result.append({
                "city": city,
                "total_pcs": data["total_pcs"],
                "online": data["online"],
                "offline": data["offline"],
                "total_labs": len(data["labs"]),
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

@stats_bp.route("/stats/city/<city>/labs", methods=["GET"])
def get_lab_stats(city):
    try:
        res = extensions.supabase.table("devices").select("city, status, last_seen, lab_name, hardware_id, cpu_score").eq("city", city).execute()
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
        res = extensions.supabase.table("devices").select("system_id, status, last_seen, hardware_id").execute()
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
            "lab_name": "Unknown"
        }).eq("city", city).execute()
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

        # Fetch all registered devices
        res = extensions.supabase.table("devices").select("*").execute()
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
