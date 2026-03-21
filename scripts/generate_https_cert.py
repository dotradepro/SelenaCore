#!/usr/bin/env python3
"""
scripts/generate_https_cert.py — Self-signed HTTPS certificate generation

Generates a self-signed TLS certificate for the SelenaCore UI server (:8080).
Certificate is stored in /secure/tls/ and used by the UI uvicorn process.

Usage: python3 scripts/generate_https_cert.py [--hostname mydevice.local]
"""
from __future__ import annotations

import argparse
import datetime
import ipaddress
import logging
import os
import socket
from pathlib import Path

logger = logging.getLogger(__name__)

CERT_DIR = Path(os.environ.get("TLS_CERT_DIR", "/secure/tls"))
CERT_FILE = CERT_DIR / "selena.crt"
KEY_FILE = CERT_DIR / "selena.key"
CERT_VALIDITY_DAYS = 3650  # 10 years


def generate_cert(hostname: str | None = None) -> tuple[Path, Path]:
    """Generate a self-signed certificate.

    Returns (cert_path, key_path).
    """
    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID
    except ImportError:
        raise RuntimeError("cryptography library not installed. Run: pip install cryptography")

    CERT_DIR.mkdir(parents=True, exist_ok=True)

    # Generate RSA key
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    # Determine hostname and IP
    device_hostname = hostname or socket.gethostname()
    try:
        local_ip = socket.gethostbyname(device_hostname)
    except Exception:
        local_ip = "127.0.0.1"

    # Build certificate
    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, device_hostname),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "SelenaCore"),
        x509.NameAttribute(NameOID.COUNTRY_NAME, "RU"),
    ])

    now = datetime.datetime.utcnow()
    san = x509.SubjectAlternativeName([
        x509.DNSName(device_hostname),
        x509.DNSName("localhost"),
        x509.DNSName("selena.local"),
        x509.IPAddress(ipaddress.ip_address(local_ip)),
        x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
    ])

    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=CERT_VALIDITY_DAYS))
        .add_extension(san, critical=False)
        .add_extension(
            x509.BasicConstraints(ca=True, path_length=None), critical=True
        )
        .sign(key, hashes.SHA256())
    )

    # Write files
    CERT_FILE.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    KEY_FILE.write_bytes(key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    ))

    # Restrict key file permissions
    KEY_FILE.chmod(0o600)

    logger.info("Certificate generated: %s", CERT_FILE)
    logger.info("Private key generated: %s", KEY_FILE)
    print(f"✓ Certificate: {CERT_FILE}")
    print(f"✓ Private key: {KEY_FILE}")
    print(f"  Hostname: {device_hostname}")
    print(f"  Valid for: {CERT_VALIDITY_DAYS // 365} years")
    return CERT_FILE, KEY_FILE


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description="Generate self-signed TLS certificate")
    parser.add_argument("--hostname", help="Device hostname (default: system hostname)")
    args = parser.parse_args()
    generate_cert(args.hostname)
