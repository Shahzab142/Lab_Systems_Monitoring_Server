# Lab Guardian Server - Works for both Local and Production (Render)
import os

# Only use gevent for production (Render) - it's optional for local dev
try:
    from gevent import monkey
    monkey.patch_all()
    GEVENT_AVAILABLE = True
except ImportError:
    GEVENT_AVAILABLE = False
    print("gevent not installed - running in simple Flask mode (fine for local dev)")

from app import create_app
from app.extensions import socketio

app = create_app()

if __name__ == "__main__":
    print(f"Server URL: http://localhost:5050")
    print(f"WebSocket: {'Enabled (gevent)' if GEVENT_AVAILABLE else 'Disabled (simple mode)'}")
    print(f"Database: Supabase")
    print("=" * 60)
    
    # Use socketio.run for WebSocket support
    socketio.run(app, debug=True, host="0.0.0.0", port=5050)
