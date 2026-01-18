from flask import Flask
from dotenv import load_dotenv
load_dotenv()
from .config import Config
from .extensions import init_extensions, socketio
from . import extensions

def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    init_extensions(app)

    # Blueprints
    from .routes.agent import agent_bp
    from .routes.devices import devices_bp
    from .routes.stats import stats_bp
    from .routes.realtime import realtime_bp

    app.register_blueprint(agent_bp, url_prefix="/api")
    app.register_blueprint(devices_bp, url_prefix="/api")
    app.register_blueprint(stats_bp, url_prefix="/api")
    app.register_blueprint(realtime_bp, url_prefix="/api")

    # Professional Landing Page
    @app.route("/")
    def index():
        return """
        <!DOCTYPE html>
        <html>
        <head>
            <title>Lab Monitoring Core v2.0</title>
            <style>
                body { background: #0a0a0c; color: #fff; font-family: 'Segoe UI', sans-serif; display: flex; align-items: center; justify-content: center; height: 100vh; margin: 0; overflow: hidden; }
                .card { background: rgba(255,255,255,0.03); border: 1px solid rgba(255,255,255,0.05); padding: 40px; border-radius: 24px; text-align: center; backdrop-filter: blur(20px); box-shadow: 0 20px 50px rgba(0,0,0,0.5); border-top: 1px solid rgba(0,255,157,0.3); }
                h1 { margin: 0; font-size: 42px; font-weight: 900; letter-spacing: -2px; italic; font-style: italic; color: #00ff9d; text-transform: uppercase; }
                p { color: rgba(255,255,255,0.5); font-size: 14px; letter-spacing: 2px; text-transform: uppercase; font-weight: bold; margin-bottom: 30px; }
                .status-badge { display: inline-flex; align-items: center; gap: 8px; background: rgba(0,255,157,0.1); color: #00ff9d; padding: 10px 20px; border-radius: 100px; font-size: 12px; font-weight: 800; border: 1px solid rgba(0,255,157,0.2); }
                .pulse { width: 8px; height: 8px; background: #00ff9d; border-radius: 50%; box-shadow: 0 0 10px #00ff9d; animation: pulse 2s infinite; }
                @keyframes pulse { 0% { opacity: 1; transform: scale(1); } 50% { opacity: 0.4; transform: scale(1.5); } 100% { opacity: 1; transform: scale(1); } }
                .footer { margin-top: 30px; font-size: 10px; color: rgba(255,255,255,0.2); font-family: monospace; }
            </style>
        </head>
        <body>
            <div class="card">
                <h1>Lab Guardian</h1>
                <p>Monitoring Server Core v2.0</p>
                <div class="status-badge">
                    <div class="pulse"></div>
                    SYSTEMS OPERATIONAL - SECURE CLOUD LINK ACTIVE
                </div>
                <div class="footer">
                    UPTIME: SYNCHRONIZED | PKT OFFSET: ACTIVE | SUPABASE: CONNECTED
                </div>
            </div>
        </body>
        </html>
        """

    # Health check
    @app.route("/health")
    def health():
        try:
            res = extensions.supabase.table("devices").select("system_id", count="exact").execute()
            return {
                "status": "ok",
                "database": "connected",
                "device_count": res.count if res.count is not None else 0
            }
        except Exception as e:
            return {
                "status": "error",
                "database": "failed",
                "message": str(e)
            }, 500

    return app
