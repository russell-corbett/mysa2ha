"""Constants for the Mysa integration."""

from __future__ import annotations

DOMAIN = "mysa"

AWS_REGION = "us-east-1"
COGNITO_USER_POOL_ID = "us-east-1_GUFWfhI7g"
COGNITO_CLIENT_ID = "19efs8tgqe942atbqmot5m36t3"
COGNITO_IDENTITY_POOL_ID = "us-east-1:ebd95d52-9995-45da-b059-56b865a18379"
COGNITO_LOGIN_KEY = f"cognito-idp.{AWS_REGION}.amazonaws.com/{COGNITO_USER_POOL_ID}"

COGNITO_IDP_ENDPOINT = f"https://cognito-idp.{AWS_REGION}.amazonaws.com/"
COGNITO_IDENTITY_ENDPOINT = "https://cognito-identity.us-east-1.amazonaws.com/"
IOT_DATA_HOST = "a3q27gia9qg3zy-ats.iot.us-east-1.amazonaws.com"
IOT_DATA_ENDPOINT = f"https://{IOT_DATA_HOST}"
API_BASE_URL = "https://app-prod.mysa.cloud"

CONF_POLL_INTERVAL = "poll_interval"
DEFAULT_POLL_INTERVAL = 10
MIN_POLL_INTERVAL = 5
MAX_POLL_INTERVAL = 600
REALTIME_TIMEOUT_SECONDS = 300
REALTIME_KEEPALIVE_SECONDS = 240

PLATFORMS = ["climate", "sensor"]

MODE_TO_RAW = {
    "off": 1,
    "auto": 2,
    "heat": 3,
    "cool": 4,
    "fan_only": 5,
    "dry": 6,
}

RAW_TO_MODE = {value: key for key, value in MODE_TO_RAW.items()}

FAN_TO_RAW = {
    "auto": 1,
    "low": 3,
    "medium": 5,
    "high": 7,
    "max": 8,
}

RAW_TO_FAN = {value: key for key, value in FAN_TO_RAW.items()}
