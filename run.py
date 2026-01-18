from gevent import monkey
monkey.patch_all()

from app import create_app
from app.extensions import socketio

app = create_app()

if __name__ == "__main__":
    # Use socketio.run for WebSocket support (dev only)
    socketio.run(app, debug=True, host="0.0.0.0", port=5010)
