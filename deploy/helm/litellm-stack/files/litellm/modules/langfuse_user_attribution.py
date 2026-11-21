import hashlib
import hmac
import os
import re
import secrets
from typing import Any

from fastapi import HTTPException
from litellm.integrations.custom_logger import CustomLogger
from litellm.proxy._types import UserAPIKeyAuth

_SESSION_ID_KEYS = ("session_id", "conversation_id", "thread_id", "run_id")
_SESSION_HEADER_KEYS = (
    "x-litellm-session-id",
    "x-session-id",
    "session-id",
    "session_id",
    "conversation_id",
    "thread-id",
    "x-conversation-id",
    "x-thread-id",
    "x-run-id",
)
_VENDOR_SESSION_HEADER = re.compile(r"^x-.+-session-id$")
_MAX_SESSION_ID_LENGTH = 256


def _validated_session_id(value: Any, source: str) -> str | None:
    if value is None:
        return None
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > _MAX_SESSION_ID_LENGTH
        or not value.isprintable()
    ):
        raise HTTPException(
            status_code=400,
            detail=f"{source} must be a printable string of 1-{_MAX_SESSION_ID_LENGTH} characters",
        )
    return value


def _metadata_session_id(metadata: dict[str, Any], source: str) -> str | None:
    for key in _SESSION_ID_KEYS:
        if key in metadata:
            return _validated_session_id(metadata[key], f"{source}.{key}")
    return None


def _header_session_id(headers: dict[Any, Any]) -> str | None:
    normalized_headers = {
        key.lower(): value for key, value in headers.items() if isinstance(key, str)
    }
    for key in _SESSION_HEADER_KEYS:
        if key in normalized_headers:
            return _validated_session_id(normalized_headers[key], f"header {key}")
    for key in sorted(normalized_headers):
        if _VENDOR_SESSION_HEADER.fullmatch(key):
            return _validated_session_id(normalized_headers[key], f"header {key}")
    return None


def _remove_raw_session_ids(metadata: dict[str, Any]) -> None:
    for key in _SESSION_ID_KEYS:
        metadata.pop(key, None)


def _scoped_session_id(
    salt: str,
    principal_type: str,
    principal_id: str,
    session_kind: str,
    raw_session_id: str,
) -> str:
    digest = hmac.new(
        salt.encode("utf-8"),
        (
            f"session:{session_kind}:{principal_type}:{principal_id}\0{raw_session_id}"
        ).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"litellm-session-{digest}"


class LangfuseUserAttribution(CustomLogger):
    async def async_pre_call_hook(
        self,
        user_api_key_dict: UserAPIKeyAuth,
        cache: Any,
        data: dict[str, Any],
        call_type: str,
    ) -> dict[str, Any]:
        proxy_server_request = data.get("proxy_server_request")
        headers: dict[Any, Any] = {}
        if isinstance(proxy_server_request, dict):
            request_headers = proxy_server_request.get("headers")
            if isinstance(request_headers, dict):
                headers = request_headers
                trusted_headers = {
                    key: value
                    for key, value in request_headers.items()
                    if not (isinstance(key, str) and key.lower().startswith("langfuse_"))
                }
                headers = trusted_headers
                if len(trusted_headers) != len(request_headers):
                    proxy_server_request = dict(proxy_server_request)
                    proxy_server_request["headers"] = trusted_headers
                    data["proxy_server_request"] = proxy_server_request

        metadata = data.get("metadata")
        if metadata is None:
            metadata = {}
        elif not isinstance(metadata, dict):
            raise HTTPException(status_code=400, detail="Request metadata must be an object")
        else:
            metadata = dict(metadata)

        has_litellm_metadata = "litellm_metadata" in data
        litellm_metadata = data.get("litellm_metadata")
        if has_litellm_metadata:
            if litellm_metadata is None:
                litellm_metadata = {}
            elif not isinstance(litellm_metadata, dict):
                raise HTTPException(
                    status_code=400,
                    detail="Request LiteLLM metadata must be an object",
                )
            else:
                litellm_metadata = dict(litellm_metadata)

        user_id = user_api_key_dict.user_id
        principal_type = "user"
        principal_id = user_id if isinstance(user_id, str) and user_id else None
        if principal_id is None:
            principal_type = "key"
            api_key = user_api_key_dict.api_key
            principal_id = api_key if isinstance(api_key, str) and api_key else None

        if principal_id is None:
            raise HTTPException(status_code=401, detail="Authenticated principal is missing")

        salt = os.environ.get("LANGFUSE_USER_ID_SALT")
        if not salt:
            raise RuntimeError("LANGFUSE_USER_ID_SALT must be configured")

        pseudonymous_id = hmac.new(
            salt.encode("utf-8"),
            f"{principal_type}:{principal_id}".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        trusted_user_id = f"litellm-{principal_type}-{pseudonymous_id}"

        raw_session_id = None
        if has_litellm_metadata:
            raw_session_id = _metadata_session_id(
                litellm_metadata,
                "litellm_metadata",
            )
        if raw_session_id is None:
            raw_session_id = _metadata_session_id(metadata, "metadata")
        if raw_session_id is None:
            raw_session_id = _validated_session_id(
                data.get("litellm_session_id"),
                "litellm_session_id",
            )
        if raw_session_id is None:
            raw_session_id = _header_session_id(headers)

        session_kind = "caller"
        if raw_session_id is None:
            session_kind = "trace"
            raw_session_id = _validated_session_id(
                data.get("litellm_trace_id")
                or (
                    litellm_metadata.get("trace_id")
                    if has_litellm_metadata
                    else metadata.get("trace_id")
                ),
                "trace_id",
            )
        if raw_session_id is None:
            session_kind = "request"
            raw_session_id = secrets.token_urlsafe(24)

        trusted_session_id = _scoped_session_id(
            salt,
            principal_type,
            principal_id,
            session_kind,
            raw_session_id,
        )

        metadata.pop("trace_user_id", None)
        _remove_raw_session_ids(metadata)
        if has_litellm_metadata:
            _remove_raw_session_ids(litellm_metadata)
            litellm_metadata["trace_user_id"] = trusted_user_id
            litellm_metadata["session_id"] = trusted_session_id
            data["litellm_metadata"] = litellm_metadata
        else:
            metadata["trace_user_id"] = trusted_user_id
            metadata["session_id"] = trusted_session_id
        if "user" in data:
            data["user"] = trusted_user_id
        data["litellm_session_id"] = trusted_session_id
        data["metadata"] = metadata
        return data


proxy_handler_instance = LangfuseUserAttribution()
