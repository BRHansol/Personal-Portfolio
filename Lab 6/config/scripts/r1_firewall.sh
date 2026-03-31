#!/bin/sh
# ══════════════════════════════════════════════════════════════════
# R1 Firewall + NAT Setup — Lab 6
# Runs inside R1 container at startup
#
# Phase 3: ACL WAN-IN   — block unsolicited inbound on eth1 (ISP)
# Phase 4: Static NAT   — expose only port 8000 to Internet
# Phase 5: IP SLA sim   — monitor 8.8.8.8, remove default if down
# ══════════════════════════════════════════════════════════════════

set -e

echo "[firewall] Starting R1 iptables setup..."

# ── Flush existing rules ─────────────────────────────────────────
iptables -F
iptables -t nat -F
iptables -t mangle -F
iptables -X 2>/dev/null || true

# ── Default policies ─────────────────────────────────────────────
iptables -P INPUT   ACCEPT
iptables -P FORWARD ACCEPT
iptables -P OUTPUT  ACCEPT

# ════════════════════════════════════════════════════════════════
# PHASE 3 — ACL WAN-IN (on eth1 = ISP WAN interface)
# Rule: only allow TCP from LAN B (192.168.20.0/24) to LAN A (192.168.10.0/24)
#       drop and log everything else inbound on WAN
# ════════════════════════════════════════════════════════════════
iptables -N WAN-IN

# Allow established/related connections (return traffic)
iptables -A WAN-IN -m state --state ESTABLISHED,RELATED -j ACCEPT

# Allow LAN B → LAN A TCP (cross-site microservice calls)
iptables -A WAN-IN -p tcp \
  -s 192.168.20.0/24 \
  -d 192.168.10.0/24 \
  -j ACCEPT

# Allow OSPF hellos on WAN link (protocol 89)
iptables -A WAN-IN -p 89 -j ACCEPT

# Log and drop everything else
iptables -A WAN-IN -j LOG --log-prefix "[WAN-IN BLOCK] " --log-level 4
iptables -A WAN-IN -j DROP

# Attach WAN-IN chain to FORWARD input on eth1
iptables -A FORWARD -i eth1 -j WAN-IN

echo "[firewall] WAN-IN ACL applied on eth1"

# ════════════════════════════════════════════════════════════════
# PHASE 4 — NAT: MASQUERADE outbound + static NAT port 8000
# ════════════════════════════════════════════════════════════════

# Outbound NAT (MASQUERADE) for LAN A → Internet via eth0
iptables -t nat -A POSTROUTING -s 192.168.10.0/24 -o eth0 -j MASQUERADE
iptables -t nat -A POSTROUTING -s 192.168.20.0/24 -o eth0 -j MASQUERADE

# Static NAT: expose ServerA Upload Service port 8000 to Internet only
# Internet → 10.255.0.2:8000  →  192.168.10.10:8000
iptables -t nat -A PREROUTING -i eth0 -p tcp --dport 8000 \
  -j DNAT --to-destination 192.168.10.10:8000

# Allow forwarding for the DNATed traffic
iptables -A FORWARD -i eth0 -p tcp -d 192.168.10.10 --dport 8000 \
  -m state --state NEW,ESTABLISHED,RELATED -j ACCEPT

# Block all other ports from Internet to LAN (ports 8001, 8002, 9000 stay private)
iptables -A FORWARD -i eth0 -d 192.168.10.0/24 -j DROP
iptables -A FORWARD -i eth0 -d 192.168.20.0/24 -j DROP

echo "[firewall] NAT MASQUERADE + static NAT port 8000 applied"

# ════════════════════════════════════════════════════════════════
# PHASE 5 — IP SLA simulation via background ping monitor
# Polls 8.8.8.8 every 5s; removes/restores default route via FRR
# ════════════════════════════════════════════════════════════════
cat > /usr/local/sbin/ip_sla_monitor.sh << 'EOF'
#!/bin/sh
# IP SLA monitor — simulates Cisco IP SLA track 1
INTERNET_GW="10.255.0.1"
PROBE_TARGET="8.8.8.8"
INTERVAL=5
ROUTE_UP=1

while true; do
    if ping -c 1 -W 2 "$PROBE_TARGET" > /dev/null 2>&1; then
        if [ "$ROUTE_UP" -eq 0 ]; then
            echo "[IP SLA] Internet reachable — restoring default route"
            vtysh -c "configure terminal" \
                  -c "ip route 0.0.0.0/0 $INTERNET_GW" \
                  -c "end" \
                  -c "write memory" 2>/dev/null || true
            ROUTE_UP=1
        fi
    else
        if [ "$ROUTE_UP" -eq 1 ]; then
            echo "[IP SLA] Internet UNREACHABLE — removing default route"
            vtysh -c "configure terminal" \
                  -c "no ip route 0.0.0.0/0 $INTERNET_GW" \
                  -c "end" \
                  -c "write memory" 2>/dev/null || true
            ROUTE_UP=0
        fi
    fi
    sleep "$INTERVAL"
done
EOF

chmod +x /usr/local/sbin/ip_sla_monitor.sh
/usr/local/sbin/ip_sla_monitor.sh &
echo "[firewall] IP SLA monitor started (PID $!)"

# ════════════════════════════════════════════════════════════════
# PHASE 5 — Observability: logging timestamps
# ════════════════════════════════════════════════════════════════
vtysh -c "configure terminal" \
      -c "log timestamp precision 3" \
      -c "end" 2>/dev/null || true

echo "[firewall] R1 setup complete"
echo ""
echo "  WAN-IN ACL   : ACTIVE on eth1"
echo "  NAT overload : ACTIVE on eth0 (LAN A + LAN B)"
echo "  Static NAT   : port 8000 -> 192.168.10.10:8000"
echo "  IP SLA       : monitoring 8.8.8.8 every ${INTERVAL}s"
