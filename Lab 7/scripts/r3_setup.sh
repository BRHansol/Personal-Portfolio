#!/bin/sh
# ══════════════════════════════════════════════════════════════════
# R3 Setup Script — Lab 7
# Phase 1: HSRP (keepalived) — Standby router
# Phase 2: Site-to-site VPN (strongSwan IPSec)
# Phase 3: iptables NAT (backup path)
# ══════════════════════════════════════════════════════════════════
set -e
echo "[R3] Starting Lab 7 setup..."

apk add --no-cache keepalived strongswan iptables iproute2 2>/dev/null || true

# ════════════════════════════════════════════════════════════════
# PHASE 1 — HSRP via keepalived (R3 = STANDBY, priority 90)
# ════════════════════════════════════════════════════════════════
cat > /etc/keepalived/keepalived.conf << 'EOF'
global_defs {
  router_id R3
}

vrrp_instance HSRP_EDGE {
  state BACKUP
  interface eth1
  virtual_router_id 10
  priority 90
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
echo "[R3] HSRP keepalived started (BACKUP, priority=90, VIP=10.0.0.254)"

# ════════════════════════════════════════════════════════════════
# PHASE 2 — Site-to-site VPN (strongSwan IPSec) — mirror of R1
# ════════════════════════════════════════════════════════════════
cat > /etc/ipsec.conf << 'EOF'
config setup
  charondebug="ike 1, knl 1, cfg 1"

conn lab7-vpn
  type=tunnel
  auto=start
  keyexchange=ikev2
  left=10.0.0.2
  leftsubnet=172.16.0.0/24
  right=10.0.0.1
  rightsubnet=192.168.10.0/24
  ike=aes256-sha256-modp2048
  esp=aes256-sha256
  ikelifetime=1h
  lifetime=8h
  dpdaction=restart
  dpddelay=30s
EOF

cat > /etc/ipsec.secrets << 'EOF'
10.0.0.2 10.0.0.1 : PSK "lab7-vpn-secret-key-2026"
EOF

chmod 600 /etc/ipsec.secrets
ipsec start 2>/dev/null || echo "[R3] IPSec startup"
echo "[R3] Site-to-site VPN configured (R3 10.0.0.2 <-> R1 10.0.0.1)"

# ════════════════════════════════════════════════════════════════
# PHASE 3 — NAT (backup, only active when R3 takes HSRP VIP)
# ════════════════════════════════════════════════════════════════
iptables -F
iptables -t nat -F
iptables -P INPUT   ACCEPT
iptables -P FORWARD ACCEPT
iptables -P OUTPUT  ACCEPT

iptables -t nat -A POSTROUTING -s 192.168.10.0/24 -o eth0 -j MASQUERADE
iptables -t nat -A POSTROUTING -s 192.168.20.0/24 -o eth0 -j MASQUERADE
iptables -t nat -A POSTROUTING -s 172.16.0.0/24   -o eth0 -j MASQUERADE
iptables -t nat -A PREROUTING  -i eth0 -p tcp --dport 80  -j DNAT --to-destination 172.16.0.10:80
iptables -t nat -A PREROUTING  -i eth0 -p tcp --dport 443 -j DNAT --to-destination 172.16.0.10:443

echo "[R3] Setup complete"
echo "  HSRP VIP    : 10.0.0.254 (BACKUP — takes over if R1 fails)"
echo "  VPN tunnel  : 10.0.0.2 <-> 10.0.0.1"
echo "  NAT         : backup path via ISP 2"
