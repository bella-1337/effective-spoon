import hashlib
import hmac
import os
from typing import Any

from fastapi import HTTPException
from litellm.integrations.custom_logger import CustomLogger
from litellm.proxy._types import UserAPIKeyAuth


class LangfuseUserAttribution(CustomLogger):
    async def async_pre_call_hook(
        self,
        user_api_key_dict: UserAPIKeyAuth,
        cache: Any,
        data: dict[str, Any],
        call_type: str,
    ) -> dict[str, Any]:
        proxy_server_request = data.get("proxy_server_request")
        if isinstance(proxy_server_request, dict):
            headers = proxy_server_request.get("headers")
            if isinstance(headers, dict):
                trusted_headers = {
                    key: value
                    for key, value in headers.items()
                    if not (isinstance(key, str) and key.lower().startswith("langfuse_"))
                }
                if len(trusted_headers) != len(headers):
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
        metadata.pop("trace_user_id", None)
        if has_litellm_metadata:
            litellm_metadata["trace_user_id"] = trusted_user_id
            data["litellm_metadata"] = litellm_metadata
        else:
            metadata["trace_user_id"] = trusted_user_id
        if "user" in data:
            data["user"] = trusted_user_id
        data["metadata"] = metadata
        return data


proxy_handler_instance = LangfuseUserAttribution()
