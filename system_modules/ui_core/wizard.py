"""
system_modules/ui_core/wizard.py — DEPRECATED

Wizard endpoints were dead code: the CoreApiProxyMiddleware intercepted
all /api/* requests, forwarding them to Core API where the real wizard
implementation lives in core/api/routes/ui.py.

This file is kept empty for backward compatibility.
"""
