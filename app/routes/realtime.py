from flask import Blueprint
from app.utils.logger import logger

# WebSocket presence is disabled as per user request (switched to heartbeat polling)
realtime_bp = Blueprint("realtime", __name__)

# This file is kept to maintain the Blueprint import in __init__.py
# but no longer contains Socket.IO logic for device status.
