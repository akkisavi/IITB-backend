"""Uniform error shape: {"detail", "code", "errors"?} for every non-2xx response (SPEC 4.0)."""
import logging

from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.db import IntegrityError
from django.http import Http404
from rest_framework import exceptions, status
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler

logger = logging.getLogger(__name__)

CONFLICT_MESSAGE = "The warehouse changed while saving. Refresh and try again."


class ApiError(exceptions.APIException):
    """Explicit error with a fixed code, e.g. ApiError(409, "location_occupied", "...")."""

    def __init__(self, status_code, code, detail, errors=None):
        self.status_code = status_code
        self.api_code = code
        self.errors = errors
        super().__init__(detail=detail, code=code)


def field_errors(errors):
    """Raise a 400 validation_error with the given {field: [messages]} dict."""
    raise ApiError(400, "validation_error", "Please correct the highlighted fields.", errors=errors)


def _flatten(detail):
    if isinstance(detail, dict):
        return {k: _flatten_list(v) for k, v in detail.items()}
    return {"non_field_errors": _flatten_list(detail)}


def _flatten_list(value):
    if isinstance(value, (list, tuple)):
        out = []
        for v in value:
            out.extend(_flatten_list(v) if isinstance(v, (list, tuple, dict)) else [str(v)])
        return out
    if isinstance(value, dict):
        return [f"{k}: {m}" for k, msgs in value.items() for m in _flatten_list(msgs)]
    return [str(value)]


def _body(detail, code, errors=None):
    body = {"detail": str(detail), "code": code}
    if errors is not None:
        body["errors"] = errors
    return body


def api_exception_handler(exc, context):
    if isinstance(exc, ApiError):
        return Response(_body(exc.detail, exc.api_code, exc.errors), status=exc.status_code)

    if isinstance(exc, IntegrityError):
        logger.warning("IntegrityError mapped to 409: %s", exc)
        return Response(_body(CONFLICT_MESSAGE, "conflict"), status=status.HTTP_409_CONFLICT)

    if isinstance(exc, Http404):
        exc = exceptions.NotFound()
    elif isinstance(exc, DjangoPermissionDenied):
        exc = exceptions.PermissionDenied()

    if isinstance(exc, exceptions.ValidationError):
        return Response(
            _body("Please correct the highlighted fields.", "validation_error", _flatten(exc.detail)),
            status=status.HTTP_400_BAD_REQUEST,
        )

    response = drf_exception_handler(exc, context)
    if response is not None:
        code_map = {
            exceptions.NotAuthenticated: "not_authenticated",
            exceptions.AuthenticationFailed: "authentication_failed",
            exceptions.PermissionDenied: "permission_denied",
            exceptions.NotFound: "not_found",
            exceptions.MethodNotAllowed: "method_not_allowed",
            exceptions.ParseError: "parse_error",
            exceptions.UnsupportedMediaType: "unsupported_media_type",
            exceptions.Throttled: "throttled",
        }
        code = next((c for cls, c in code_map.items() if isinstance(exc, cls)), None)
        if code is None:
            code = getattr(exc, "default_code", "error")
        detail = exc.detail if not isinstance(exc.detail, (list, dict)) else str(exc.detail)
        if isinstance(exc, exceptions.PermissionDenied):
            detail = "You do not have permission to perform this action."
        response.data = _body(detail, code)
        return response

    logger.exception("Unhandled API error", exc_info=exc)
    return Response(_body("Internal server error.", "server_error"), status=status.HTTP_500_INTERNAL_SERVER_ERROR)
