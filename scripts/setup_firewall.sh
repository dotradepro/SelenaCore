#!/usr/bin/env bash
# scripts/setup_firewall.sh — iptables rules for SelenaCore
# Run as root: sudo bash scripts/setup_firewall.sh

set -euo pipefail

echo "[firewall] Setting up iptables rules for SelenaCore..."

# Flush existing rules
iptables -F INPUT
iptables -F FORWARD
iptables -F OUTPUT

# Default policies
iptables -P INPUT DROP
iptables -P FORWARD DROP
iptables -P OUTPUT ACCEPT

# Allow loopback
iptables -A INPUT -i lo -j ACCEPT

# Allow established/related connections
iptables -A INPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT

# Allow SSH (adjust port if needed)
iptables -A INPUT -p tcp --dport 22 -j ACCEPT

# Core API (local network only)
iptables -A INPUT -p tcp --dport 7070 -m conntrack --ctstate NEW -j ACCEPT

# UI (local network only)
iptables -A INPUT -p tcp --dport 8080 -m conntrack --ctstate NEW -j ACCEPT

# Block external access to module ports (8100-8200)
# Modules are only accessible from internal docker network
iptables -A INPUT -p tcp --dport 8100:8200 -s 127.0.0.1 -j ACCEPT
iptables -A INPUT -p tcp --dport 8100:8200 -s 172.16.0.0/12 -j ACCEPT
iptables -A INPUT -p tcp --dport 8100:8200 -j DROP

# Block external access to Ollama
iptables -A INPUT -p tcp --dport 11434 -s 127.0.0.1 -j ACCEPT
iptables -A INPUT -p tcp --dport 11434 -j DROP

# Save rules
iptables-save > /etc/iptables/rules.v4

echo "[firewall] Rules saved to /etc/iptables/rules.v4"
echo "[firewall] Done."
