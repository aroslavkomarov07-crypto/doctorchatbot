import hashlib
import hmac
from time import time
from urllib.parse import parse_qsl


def verify_mini_app_init_data(init_data: str, bot_token: str, *, max_age: int = 3600) -> bool:
    """Verify Telegram Mini App initData for a future HTTP client."""
    values = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = values.pop("hash", "")
    auth_date = int(values.get("auth_date", "0") or 0)
    if not received_hash or auth_date <= 0 or time() - auth_date > max_age:
        return False
    data_check_string = "\n".join(f"{key}={values[key]}" for key in sorted(values))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret, data_check_string.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, received_hash)
