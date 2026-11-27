"""Idempotency-Key support for stock operations (load/unload).

The client sends `Idempotency-Key: <8-64 chars of [A-Za-z0-9_-]>`. The first successful response
is stored per (user, key). A retry with the same key and the same body returns that stored
response with `Idempotent-Replayed: true`, so a double-click or a retried request after a lost
response cannot load a second pallet or unload twice. The header is optional.
"""
import hashlib
import json
import re

from django.db import IntegrityError, transaction
from rest_framework.response import Response

from .exceptions import ApiError
from .models import IdempotencyRecord

KEY_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


def _hash(data):
    return hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()


def idempotent(request, endpoint, run):
    """run() -> (body, status). Returns a DRF Response, replaying a stored one when appropriate."""
    key = request.headers.get("Idempotency-Key", "").strip()
    if not key:
        body, code = run()
        return Response(body, status=code)
    if not KEY_RE.match(key):
        raise ApiError(400, "validation_error", "Idempotency-Key must be 8-64 letters, digits, '-' or '_'.")

    req_hash = _hash(request.data)
    prior = IdempotencyRecord.objects.filter(user=request.user, key=key).first()
    if prior:
        if prior.endpoint != endpoint or prior.request_hash != req_hash:
            raise ApiError(
                422, "idempotency_key_reused", "This Idempotency-Key was already used for a different request."
            )
        return Response(prior.response_body, status=prior.response_status, headers={"Idempotent-Replayed": "true"})

    body, code = run()
    try:
        with transaction.atomic():
            IdempotencyRecord.objects.create(
                user=request.user, key=key, endpoint=endpoint, request_hash=req_hash,
                response_status=code, response_body=body,
            )
    except IntegrityError:
        # A concurrent request with the same key finished first. Our operation still succeeded
        # (or the stock checks would have stopped it), so return our own result.
        pass
    return Response(body, status=code)
