"""Plejd native provider.

Three layers:
    - ``crypto`` — AES-128-ECB keystream (symmetric, address-bound)
    - ``cloud``  — one-time auth against hems.plejd.com/parse to fetch
                   site_key + device list
    - ``gateway``— persistent BLE GATT connection to one mesh node,
                   with reconnect/backoff and arbiter-aware leases

The driver (``drivers/plejd.py``) is a thin shell that delegates every
operation to the gateway singleton.
"""
