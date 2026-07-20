"""
Central configuration. Everything that varies between sandbox/live or between
developer machines lives here, loaded from environment variables (.env locally).
Never hardcode secrets anywhere else in the codebase.
"""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Monnify
    monnify_base_url: str = "https://sandbox.monnify.com"
    monnify_api_key: str = ""
    monnify_secret_key: str = ""
    monnify_contract_code: str = ""
    monnify_source_account_number: str = ""

    # Database
    database_url: str = "sqlite:///./padipot.db"

    # WhatsApp Cloud API (Meta, direct)
    whatsapp_token: str = ""
    whatsapp_phone_number_id: str = ""
    whatsapp_verify_token: str = "padipot_verify_me"
    whatsapp_api_version: str = "v20.0"

    # WhatsApp via Twilio (alternative transport — see app/channels/whatsapp/twilio_*)
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_whatsapp_from: str = ""  # e.g. "whatsapp:+14155238886" (sandbox) or your approved sender
    public_base_url: str = ""  # e.g. "https://your-ngrok-domain.ngrok-free.app" — used for Twilio signature checks

    # Africa's Talking
    at_username: str = "sandbox"
    at_api_key: str = ""
    at_sender_id: str = "PADIPOT"

    # App behaviour
    default_language: str = "en"
    guard_sweep_interval_seconds: int = 120
    reminder_check_interval_seconds: int = 3600
    log_level: str = "INFO"


settings = Settings()
