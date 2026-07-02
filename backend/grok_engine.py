"""Compatibility shim — all functionality moved to ai_engine.py."""
from ai_engine import *  # noqa: F401,F403
from ai_engine import _ai_call as _grok_call, _ai_search_call as _grok_search_call  # noqa: F401
