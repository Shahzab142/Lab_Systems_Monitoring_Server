import uuid
from datetime import datetime
from app.extensions import supabase

def create_alert(device_id, alert_type, message, severity="warning"):
    supabase.table("alerts").insert({
        "id": str(uuid.uuid4()),
        "device_id": device_id,
        "type": alert_type,
        "message": message,
        "severity": severity,
        "created_at": datetime.utcnow().isoformat(),
        "resolved": False
    }).execute()
