"""
system_modules/network_scanner/classifier.py — Device type classification

Combines ARP, mDNS, SSDP, and OUI data to produce a unified device list
with inferred device types (smart_speaker, light, camera, hub, phone, etc.).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from .oui_lookup import lookup as oui_lookup

logger = logging.getLogger(__name__)

# Keyword-based type inference rules (service_type or manufacturer)
TYPE_RULES: list[tuple[list[str], str]] = [
    (["googlecast", "chromecast"], "smart_speaker"),
    (["airplay", "raop"], "smart_speaker"),
    (["hap", "homekit"], "smart_home_hub"),
    (["home-assistant", "homeassistant"], "smart_home_hub"),
    (["philips", "hue"], "smart_light"),
    (["esphome", "tasmota"], "smart_plug"),
    (["ring", "arlo", "reolink", "hikvision", "dahua"], "camera"),
    (["sonos", "bose", "denon"], "smart_speaker"),
    (["printer", "ipp", "jetdirect"], "printer"),
    (["android", "iphone", "apple"], "phone"),
    (["raspberry", "pi"], "sbc"),
    (["router", "gateway", "openwrt", "mikrotik"], "router"),
    (["synology", "qnap", "nas"], "nas"),
]


@dataclass
class DiscoveredDevice:
    ip: str
    mac: str
    manufacturer: str
    device_type: str
    hostnames: list[str] = field(default_factory=list)
    services: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


def _infer_type(manufacturer: str, services: list[str]) -> str:
    combined = (manufacturer + " " + " ".join(services)).lower()
    for keywords, dtype in TYPE_RULES:
        if any(k in combined for k in keywords):
            return dtype
    return "unknown"


def classify_devices(
    arp_entries: list[Any],
    mdns_devices: list[Any],
    ssdp_devices: list[Any],
) -> list[DiscoveredDevice]:
    """Merge and classify all discovered devices into DiscoveredDevice list."""
    # Build IP → DiscoveredDevice index from ARP
    by_ip: dict[str, DiscoveredDevice] = {}

    for entry in arp_entries:
        manufacturer = oui_lookup(entry.mac)
        dev = DiscoveredDevice(
            ip=entry.ip,
            mac=entry.mac,
            manufacturer=manufacturer,
            device_type="unknown",
        )
        by_ip[entry.ip] = dev

    # Enrich from mDNS
    for mdns in mdns_devices:
        if mdns.address in by_ip:
            dev = by_ip[mdns.address]
        else:
            dev = DiscoveredDevice(
                ip=mdns.address,
                mac="",
                manufacturer=oui_lookup("000000"),
                device_type="unknown",
            )
            by_ip[mdns.address] = dev

        if mdns.hostname and mdns.hostname not in dev.hostnames:
            dev.hostnames.append(mdns.hostname)
        svc = f"{mdns.service_type}/{mdns.name}"
        if svc not in dev.services:
            dev.services.append(svc)
        dev.metadata.update(mdns.properties or {})

    # Enrich from SSDP
    for ssdp in ssdp_devices:
        if ssdp.address in by_ip:
            dev = by_ip[ssdp.address]
            if ssdp.st not in dev.services:
                dev.services.append(ssdp.st)
            if ssdp.server and ssdp.server not in dev.metadata.get("ssdp_server", ""):
                dev.metadata["ssdp_server"] = ssdp.server
            dev.metadata["ssdp_location"] = ssdp.location

    # Final classification pass
    for dev in by_ip.values():
        dev.device_type = _infer_type(dev.manufacturer, dev.services)

    return list(by_ip.values())
