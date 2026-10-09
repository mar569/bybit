"""HMAC signing for Liquid Trading public API (compatible with liquidtrading-python)."""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
from urllib.parse import parse_qsl, quote, urlparse

HEADER_API_KEY = "X-Liquid-Key"
HEADER_SIGNATURE = "X-Liquid-Signature"
HEADER_TIMESTAMP = "X-Liquid-Timestamp"
HEADER_NONCE = "X-Liquid-Nonce"


def canonicalize_path(path: str) -> str:
    parsed = urlparse(path)
    clean = parsed.path.rstrip("/").lower()
    return clean if clean else "/"


def canonicalize_query(query_string: str) -> str:
    if not query_string:
        return ""
    params = parse_qsl(query_string, keep_blank_values=True)
    sorted_params = sorted(params, key=lambda x: (x[0], x[1]))
    return "&".join(
        f"{quote(k, safe='-_.~')}={quote(v, safe='-_.~')}"
        for k, v in sorted_params
    )


def compute_body_hash(body: bytes | None) -> str:
    if body is None or len(body) == 0:
        return hashlib.sha256(b"").hexdigest()
    try:
        parsed = json.loads(body)
        canonical = json.dumps(parsed, separators=(",", ":"), sort_keys=True).encode()
        return hashlib.sha256(canonical).hexdigest()
    except (json.JSONDecodeError, UnicodeDecodeError):
        return hashlib.sha256(body).hexdigest()


def compute_signature(
    secret: str,
    timestamp: str,
    nonce: str,
    method: str,
    path: str,
    query: str,
    body_hash: str,
) -> str:
    message = f"{timestamp}\n{nonce}\n{method.upper()}\n{path}\n{query}\n{body_hash}"
    return hmac.new(
        secret.encode("utf-8"),
        message.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def sign_request(
    api_secret: str,
    method: str,
    path: str,
    query_string: str,
    body: bytes | None,
) -> dict[str, str]:
    timestamp = str(int(time.time() * 1000))
    nonce = secrets.token_hex(16)
    canonical_path = canonicalize_path(path)
    canonical_query = canonicalize_query(query_string)
    body_hash = compute_body_hash(body)
    signature = compute_signature(
        secret=api_secret,
        timestamp=timestamp,
        nonce=nonce,
        method=method.upper(),
        path=canonical_path,
        query=canonical_query,
        body_hash=body_hash,
    )
    return {
        HEADER_TIMESTAMP: timestamp,
        HEADER_NONCE: nonce,
        HEADER_SIGNATURE: signature,
    }
