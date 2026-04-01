#!/bin/sh
# ══════════════════════════════════════════════════════════════════
# R1 Setup Script — Lab 7
# Phase 1: HSRP (keepalived) — Active router
# Phase 2: Site-to-site VPN (strongSwan IPSec)
# Phase 3: iptables WAN-IN ACL + NAT + static NAT
# Phase 5: IP SLA monitor
# ══════════════════════════════════════════════════════════════════
set -e
echo "[R1] Starting Lab 7 setup..."

# ── Install tools ─────────────────────────────────────────────────
apk add --no-cache keepalived strongswan iptables iproute2 2>/dev/null || true

# ════════════════════════════════════════════════════════════════
# PHASE 1 — HSRP via keepalived (R1 = ACTIVE, priority 110)
# VIP: 10.0.0.254 (shared gateway for DMZ + LAN A)
# ════════════════════════════════════════════════════════════════
cat > /etc/keepalived/keepalived.conf << 'EOF'
global_defs {
  router_id R1
}

vrrp_instance HSRP_EDGE {
  state MASTER
  interface eth1
  virtual_router_id 10
  priority 110
  advert_int 1
  authentication {
    auth_type PASS
    auth_pass lab7hsrp
  }
  virtual_ipaddress {
    10.0.0.254/29
  }
  track_interface {
    eth0 weight -20
  }
}
EOF

keepalived -f /etc/keepalived/keepalived.conf -D &
echo "[R1] HSRP keepalived started (MASTER, priority=110, VIP=10.0.0.254)"

# ════════════════════════════════════════════════════════════════
# PHASE 2 — Site-to-site VPN (strongSwan IPSec)
# R1 (10.0.0.1) <--> R3 (10.0.0.2)
# Encrypts traffic between LAN A and DMZ subnets
# ════════════════════════════════════════════════════════════════
cat > /etc/ipsec.conf << 'EOF'
config setup
  charondebug="ike 1, knl 1, cfg 1"

conn lab7-vpn
  type=tunnel
  auto=start
  keyexchange=ikev2
  left=10.0.0.1
  leftsubnet=192.168.10.0/24
  right=10.0.0.2
  rightsubnet=172.16.0.0/24
  ike=aes256-sha256-modp2048
  esp=aes256-sha256
  ikelifetime=1h
  lifetime=8h
  dpdaction=restart
  dpddelay=30s
EOF

cat > /etc/ipsec.secrets << 'EOF'
10.0.0.1 10.0.0.2 : PSK "lab7-vpn-secret-key-2026"
EOF

chmod 600 /etc/ipsec.secrets
ipsec start 2>/dev/null || echo "[R1] IPSec startup (may need kernel modules)"
echo "[R1] Site-to-site VPN configured (R1 10.0.0.1 <-> R3 10.0.0.2)"

# ════════════════════════════════════════════════════════════════
# PHASE 3 — iptables: flush + WAN-IN ACL + NAT
# ════════════════════════════════════════════════════════════════
iptables -F
iptables -t nat -F
iptables -t mangle -F
iptables -X 2>/dev/null || true

iptables -P INPUT   ACCEPT
iptables -P FORWARD ACCEPT
iptables -P OUTPUT  ACCEPT

# WAN-IN chain on eth0 (Internet)
iptables -N WAN-IN
iptables -A WAN-IN -m state --state ESTABLISHED,RELATED -j ACCEPT
iptables -A WAN-IN -p tcp --dport 80  -j ACCEPT   # HTTP to LB
iptables -A WAN-IN -p tcp --dport 443 -j ACCEPT   # HTTPS to LB
iptables -A WAN-IN -p tcp --dport 3000 -j ACCEPT  # Grafana
iptables -A WAN-IN -p 89  -j ACCEPT               # OSPF
iptables -A WAN-IN -j LOG --log-prefix "[WAN-IN DROP] " --log-level 4
iptables -A WAN-IN -j DROP
iptables -A FORWARD -i eth0 -j WAN-IN

# MASQUERADE outbound
iptables -t nat -A POSTROUTING -s 192.168.10.0/24 -o eth0 -j MASQUERADE
iptables -t nat -A POSTROUTING -s 192.168.20.0/24 -o eth0 -j MASQUERADE
iptables -t nat -A POSTROUTING -s 172.16.0.0/24   -o eth0 -j MASQUERADE

# Static NAT: forward port 80/443 to Load Balancer in DMZ
iptables -t nat -A PREROUTING -i eth0 -p tcp --dport 80  -j DNAT --to-destination 172.16.0.10:80
iptables -t nat -A PREROUTING -i eth0 -p tcp --dport 443 -j DNAT --to-destination 172.16.0.10:443
iptables -t nat -A PREROUTING -i eth0 -p tcp --dport 3000 -j DNAT --to-destination 172.16.0.30:3000

echo "[R1] WAN-IN ACL + NAT + static NAT (80/443→LB, 3000→Grafana)"

# ════════════════════════════════════════════════════════════════
# PHASE 5 — IP SLA monitor (same as Lab 6)
# ════════════════════════════════════════════════════════════════
cat > /usr/local/sbin/ip_sla_monitor.sh << 'EOF'
#!/bin/sh
PROBE_TARGET="8.8.8.8"
INTERNET_GW="10.255.0.1"
INTERVAL=5
ROUTE_UP=1

while true; do
  if ping -c 1 -W 2 "$PROBE_TARGET" > /dev/null 2>&1; then
    if [ "$ROUTE_UP" -eq 0 ]; then
      echo "[IP SLA] Internet restored — adding default route"
      vtysh -c "configure terminal" -c "ip route 0.0.0.0/0 $INTERNET_GW" -c "end" -c "write memory" 2>/dev/null || true
      ROUTE_UP=1
    fi
  else
    if [ "$ROUTE_UP" -eq 1 ]; then
      echo "[IP SLA] Internet UNREACHABLE — removing default route (R3 takes over via HSRP)"
      vtysh -c "configure terminal" -c "no ip route 0.0.0.0/0 $INTERNET_GW" -c "end" -c "write memory" 2>/dev/null || true
      ROUTE_UP=0
    fi
  fi
  sleep "$INTERVAL"
done
EOF
chmod +x /usr/local/sbin/ip_sla_monitor.sh
/usr/local/sbin/ip_sla_monitor.sh &

echo "[R1] Setup complete"
echo "  HSRP VIP    : 10.0.0.254 (MASTER)"
echo "  VPN tunnel  : 10.0.0.1 <-> 10.0.0.2"
echo "  NAT         : LAN A+B+DMZ -> eth0"
echo "  LB forward  : :80/:443 -> 172.16.0.10"
echo "  IP SLA      : monitoring $PROBE_TARGET every ${INTERVAL}s"
