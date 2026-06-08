"""Backward-compatible import shim for xconv2.xconvcf."""

from __future__ import annotations

from .xconvcf import *  # noqa: F401,F403
from .xconvcf import __all__ as _XCONVCF_ALL

__all__ = _XCONVCF_ALL
