"""
system_modules/ui_core/routes/dashboard.py — DEPRECATED

These routes were dead code: the CoreApiProxyMiddleware intercepted all /api/*
requests before they reached these handlers, forwarding them to Core API.

All dashboard, device, module, and system endpoints are now served directly
by core/api/routes/ui.py. This file is kept empty for backward compatibility.
"""
