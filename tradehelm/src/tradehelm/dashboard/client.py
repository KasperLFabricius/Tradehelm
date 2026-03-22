"""API client helpers for the dashboard."""
from __future__ import annotations

from dataclasses import dataclass

import requests


@dataclass
class ApiResult:
    ok: bool
    payload: dict | list | None = None
    error: str | None = None


def call_api(base_url: str, method: str, path: str, payload: dict | None = None) -> ApiResult:
    try:
        resp = requests.request(method, f"{base_url}{path}", json=payload or {}, timeout=15)
        data = resp.json() if resp.content else {}
    except requests.RequestException as exc:
        return ApiResult(ok=False, error=f"Connection error: {exc}")
    except ValueError:
        return ApiResult(ok=False, error="API returned non-JSON response")

    if resp.status_code >= 400:
        if isinstance(data, dict) and "error" in data:
            return ApiResult(ok=False, error=f"{data['error'].get('code')}: {data['error'].get('message')}")
        return ApiResult(ok=False, error=f"HTTP {resp.status_code}")
    return ApiResult(ok=True, payload=data)
