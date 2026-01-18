import jwt
from flask import request, abort, current_app

def verify_supabase_jwt():
    # BYPASS FOR LOCAL TESTING
    return {"user_id": "testing"}
    
    auth = request.headers.get("Authorization")
    if not auth or not auth.startswith("Bearer "):
        # For local testing, we bypass authentication
        return {} # abort(401, "Missing token")

    token = auth.split(" ")[1]

    try:
        payload = jwt.decode(
            token,
            current_app.config["SUPABASE_JWT_SECRET"],
            algorithms=["HS256"],
            audience="authenticated"
        )
        return payload
    except jwt.ExpiredSignatureError:
        abort(401, "Token expired")
    except jwt.InvalidTokenError:
        abort(401, "Invalid token")
