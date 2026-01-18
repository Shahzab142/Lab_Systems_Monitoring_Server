import os

class Config:
    SUPABASE_URL = os.getenv("SUPABASE_URL")
    SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY")
    SUPABASE_JWT_SECRET = os.getenv("SUPABASE_JWT_SECRET")

    CORS_ORIGINS = os.getenv("CORS_ORIGINS", "*")
