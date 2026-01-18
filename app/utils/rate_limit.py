from time import time
from flask import request, abort

REQUESTS = {}

def rate_limit(limit=60):
    ip = request.remote_addr
    now = int(time())

    REQUESTS.setdefault(ip, [])
    # Filter out requests older than 60 seconds
    REQUESTS[ip] = [t for t in REQUESTS[ip] if now - t < 60]

    if len(REQUESTS[ip]) >= limit:
        abort(429, "Too many requests")

    REQUESTS[ip].append(now)
