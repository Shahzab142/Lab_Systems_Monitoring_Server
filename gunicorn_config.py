import os

# Gunicorn configuration settings
bind = "0.0.0.0:" + os.environ.get("PORT", "5050")
workers = 1
worker_class = "geventwebsocket.gunicorn.workers.GeventWebSocketWorker"
timeout = 120
keepalive = 5
threads = 4
worker_connections = 1000
loglevel = "info"
accesslog = "-"
errorlog = "-"
proc_name = "lab_guardian_api"
preload_app = True
