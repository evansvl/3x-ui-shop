import os
import uuid
import dotenv
from datetime import datetime, timedelta
import logging

from py3xui import Api, Client, Inbound
from . import otp

dotenv.load_dotenv()
logger = logging.getLogger(__name__)

xui_host = os.getenv("XUI_HOST")
xui_username = os.getenv("XUI_USERNAME")
xui_password = os.getenv("XUI_PASSWORD")
totp = os.getenv("XUI_TOTP")
should_use_totp = bool(totp)
MAIN_REMARK = os.getenv("MAIN_REMARK")
port = os.getenv("XUI_PORT")

def login() -> tuple[Api | None, Inbound | None]:
    api = None
    target_inbound = None

    try:
        if should_use_totp:
            api = Api.from_env()
            print(should_use_totp)
            print(f"DEBUG: Value of 'TOTP_SECRET': {os.getenv('TOTP')}")
            api.login()
        else:
            api = Api.from_env()
            otp_code = otp.getTOTP()
            if not otp_code:
                raise ValueError("Failed to generate OTP code.")
            logger.info(f"Using OTP code: {otp_code}")
            api.login(otp_code)
            
            inbounds: list[Inbound] = api.inbound.get_list()
            for inbound in inbounds:
                if inbound.remark == MAIN_REMARK:
                    target_inbound = inbound
                    break
            if target_inbound is None:
                logger.error(f"No inbound found with remark '{MAIN_REMARK}'")
                return api, None
            return api, target_inbound
    except Exception as e:
        logger.error(f"Login or inbound retrieval failed: {e}", exc_info=True)
        return None, None

def get_connection_string(inbound: Inbound, user_uuid: str, user_email: str) -> str | None:
    if not inbound: return None
    settings = inbound.stream_settings.reality_settings.get("settings")
    if not settings: return None
    public_key = settings.get("publicKey")
    server_names = inbound.stream_settings.reality_settings.get("serverNames")
    short_ids = inbound.stream_settings.reality_settings.get("shortIds")
    if not all([public_key, server_names, short_ids]): return None
    
    fp = os.getenv("FP")
    sni = os.getenv("SNI")
    website_name = server_names[0]
    short_id = short_ids[0]
    
    connection_string = (
        f"vless://{user_uuid}@{website_name}:{port}"
        f"?type=tcp&security=reality&pbk={public_key}&fp={fp}&sni={sni}"
        f"&sid={short_id}&spx=%2F#{MAIN_REMARK}-{user_email}"
    )
    return connection_string

def get_client_by_email(email: str, api: Api) -> Client | None:
    try:
        client = api.client.get_by_email(email)
        return client if client else None
    except Exception:
        return None

def update_or_create_client(api: Api, inbound: Inbound, email: str, days_to_add: int):
    try:
        existing_client = None
        full_inbound = api.inbound.get_by_id(inbound.id)
        if full_inbound.settings and full_inbound.settings.clients:
            for c in full_inbound.settings.clients:
                if c.email == email:
                    existing_client = c
                    break

        if existing_client and existing_client.expiry_time > int(datetime.now().timestamp() * 1000):
            current_expiry = datetime.fromtimestamp(existing_client.expiry_time / 1000)
            new_expiry_dt = current_expiry + timedelta(days=days_to_add)
        else:
            new_expiry_dt = datetime.now() + timedelta(days=days_to_add)
        
        new_expiry_ms = int(new_expiry_dt.timestamp() * 1000)

        if existing_client:
            client_to_update = api.client.get_by_email(email)
            if not client_to_update:
                raise ValueError(f"Could not get client by email '{email}' for update")
            
            client_to_update.expiry_time = new_expiry_ms
            client_to_update.total_gb = 0
            client_to_update.enable = True
            client_to_update.id = existing_client.id
            
            api.client.update(client_to_update.id, client_to_update)
            return existing_client.id, new_expiry_ms
        else:
            user_uuid = str(uuid.uuid4())
            new_client = Client(id=user_uuid, email=email, enable=True, expiry_time=new_expiry_ms, total_gb=0)
            api.client.add(inbound.id, [new_client])
            return user_uuid, new_expiry_ms

    except Exception as e:
        print(f"Error in update_or_create_client: {e}")
        return None, None