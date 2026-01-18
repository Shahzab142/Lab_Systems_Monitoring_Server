from flask_cors import CORS
from supabase import create_client
from flask_socketio import SocketIO

supabase = None
socketio = SocketIO()

def init_extensions(app):
    global supabase

    CORS(
        app,
        resources={r"/*": {
            "origins": "*"
        }},
        supports_credentials=True
    )

    supabase = create_client(
        app.config["SUPABASE_URL"],
        app.config["SUPABASE_SERVICE_KEY"]
    )

    socketio.init_app(
        app, 
        cors_allowed_origins="*",
        async_mode='gevent',
        ping_timeout=60,
        ping_interval=25
    )
