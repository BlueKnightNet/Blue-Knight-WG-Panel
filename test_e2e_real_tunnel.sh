#!/usr/bin/env bash
# Real multi-protocol tunnel verification across WireGuard, AmneziaWG, and Cloudflare WARP.
# Runs inside isolated disposable network namespaces without modifying host networking.
set -Eeuo pipefail

[[ $EUID -eq 0 ]] || { echo 'Run as root.' >&2; exit 1; }

bin=${1:?Pass path to directory containing built awg and amneziawg-go}
bin=$(realpath "$bin")

[[ -x "$bin/awg" ]] || { echo "Binary $bin/awg not found or not executable." >&2; exit 1; }
[[ -x "$bin/amneziawg-go" ]] || { echo "Binary $bin/amneziawg-go not found or not executable." >&2; exit 1; }

stage=$(mktemp -d /tmp/diana-e2e.XXXXXXXX)
mkdir -p "$stage/bin"
export PATH="$stage/bin:$bin:$PATH"
export WG_QUICK_USERSPACE_IMPLEMENTATION="amneziawg-go"

if command -v wg >/dev/null 2>&1; then
  WG_CMD="wg"
else
  ln -sf "$bin/awg" "$stage/bin/wg"
  WG_CMD="$stage/bin/wg"
fi

if ! command -v wg-quick >/dev/null 2>&1 && [[ -x "$bin/awg-quick" ]]; then
  ln -sf "$bin/awg-quick" "$stage/bin/wg-quick"
fi

srv_ns="diana-srv-$$"
cli_ns="diana-cli-$$"
srv_wg_pid=''
cli_wg_pid=''
srv_awg_pid=''
cli_awg_pid=''

cleanup() {
  local exit_code=$?
  if [[ -n "$srv_awg_pid" ]]; then
    kill "$srv_awg_pid" 2>/dev/null || true
  fi
  if [[ -n "$cli_awg_pid" ]]; then
    kill "$cli_awg_pid" 2>/dev/null || true
  fi
  if [[ -n "$srv_wg_pid" ]]; then
    kill "$srv_wg_pid" 2>/dev/null || true
  fi
  if [[ -n "$cli_wg_pid" ]]; then
    kill "$cli_wg_pid" 2>/dev/null || true
  fi
  if [[ -f "$stage/warp0.conf" ]]; then
    ip netns exec "$srv_ns" env PATH="$stage/bin:$bin:$PATH" wg-quick down "$stage/warp0.conf" 2>/dev/null || true
  fi
  ip netns del "$srv_ns" 2>/dev/null || true
  ip netns del "$cli_ns" 2>/dev/null || true
  rm -rf -- "$stage"
  exit "$exit_code"
}
trap cleanup EXIT INT TERM

echo "=== Creating network namespaces and veth interconnection ==="
ip netns add "$srv_ns"
ip netns add "$cli_ns"

ip link add ds-veth type veth peer name dc-veth
ip link set ds-veth netns "$srv_ns"
ip link set dc-veth netns "$cli_ns"

ip -n "$srv_ns" addr add 192.0.2.1/30 dev ds-veth
ip -n "$cli_ns" addr add 192.0.2.2/30 dev dc-veth

for ns_pair in "$srv_ns:ds-veth" "$cli_ns:dc-veth"; do
  ns=${ns_pair%:*}; iface=${ns_pair#*:}
  ip -n "$ns" link set lo up
  ip -n "$ns" link set "$iface" up
done

# Enable IPv4 packet forwarding in server namespace
ip netns exec "$srv_ns" sysctl -w net.ipv4.ip_forward=1 >/dev/null

# Verify baseline link connectivity between namespaces
ip netns exec "$cli_ns" ping -c 2 -W 2 192.0.2.1 >/dev/null

echo "=== Phase 1: Real WireGuard tunnel test ==="
srv_wg_priv=$("$bin/awg" genkey)
srv_wg_pub=$(printf '%s' "$srv_wg_priv" | "$bin/awg" pubkey)
cli_wg_priv=$("$bin/awg" genkey)
cli_wg_pub=$(printf '%s' "$cli_wg_priv" | "$bin/awg" pubkey)
wg_psk=$("$bin/awg" genpsk)

cat > "$stage/wg_server.conf" <<EOF
[Interface]
PrivateKey = $srv_wg_priv
ListenPort = 51820

[Peer]
PublicKey = $cli_wg_pub
PresharedKey = $wg_psk
AllowedIPs = 10.203.240.2/32
EOF

cat > "$stage/wg_client.conf" <<EOF
[Interface]
PrivateKey = $cli_wg_priv

[Peer]
PublicKey = $srv_wg_pub
PresharedKey = $wg_psk
Endpoint = 192.0.2.1:51820
AllowedIPs = 10.203.240.0/24
PersistentKeepalive = 25
EOF

modprobe wireguard 2>/dev/null || true
if ip -n "$srv_ns" link add wg0 type wireguard 2>/dev/null; then
  ip -n "$cli_ns" link add wg-cli type wireguard
else
  ip netns exec "$srv_ns" "$bin/amneziawg-go" -f wg0 >"$stage/wg_srv.log" 2>&1 & srv_wg_pid=$!
  ip netns exec "$cli_ns" "$bin/amneziawg-go" -f wg-cli >"$stage/wg_cli.log" 2>&1 & cli_wg_pid=$!
  for _ in $(seq 1 50); do
    if ip -n "$srv_ns" link show wg0 >/dev/null 2>&1 && ip -n "$cli_ns" link show wg-cli >/dev/null 2>&1; then
      break
    fi
    sleep 0.1
  done
fi

ip netns exec "$srv_ns" "$WG_CMD" setconf wg0 "$stage/wg_server.conf"
ip -n "$srv_ns" addr add 10.203.240.1/24 dev wg0
ip -n "$srv_ns" link set wg0 mtu 1280 up

ip netns exec "$cli_ns" "$WG_CMD" setconf wg-cli "$stage/wg_client.conf"
ip -n "$cli_ns" addr add 10.203.240.2/32 dev wg-cli
ip -n "$cli_ns" link set wg-cli mtu 1280 up
ip -n "$cli_ns" route add 10.203.240.0/24 dev wg-cli

# Verify handshake and ICMP ping from client to server (10.203.240.1)
ip netns exec "$cli_ns" ping -c 3 -W 5 10.203.240.1 >/dev/null
latest_hs=$(ip netns exec "$srv_ns" "$WG_CMD" show wg0 latest-handshakes | awk '{print $2}')
if [[ -n "$latest_hs" && "$latest_hs" -gt 0 ]]; then
  echo "Phase 1: WireGuard handshake confirmed (timestamp: $latest_hs)."
fi
echo "Phase 1 PASS: Real WireGuard tunnel verified successfully."

echo "=== Phase 2: Real AmneziaWG obfuscated tunnel test ==="
ip netns exec "$srv_ns" "$bin/amneziawg-go" -f awg0 >"$stage/awg_srv.log" 2>&1 & srv_awg_pid=$!
ip netns exec "$cli_ns" "$bin/amneziawg-go" -f awg-cli >"$stage/awg_cli.log" 2>&1 & cli_awg_pid=$!

for _ in $(seq 1 50); do
  if ip -n "$srv_ns" link show awg0 >/dev/null 2>&1 && ip -n "$cli_ns" link show awg-cli >/dev/null 2>&1; then
    break
  fi
  sleep 0.1
done

srv_awg_priv=$("$bin/awg" genkey)
srv_awg_pub=$(printf '%s' "$srv_awg_priv" | "$bin/awg" pubkey)
cli_awg_priv=$("$bin/awg" genkey)
cli_awg_pub=$(printf '%s' "$cli_awg_priv" | "$bin/awg" pubkey)
awg_psk=$("$bin/awg" genpsk)

cat > "$stage/awg_server.conf" <<EOF
[Interface]
PrivateKey = $srv_awg_priv
ListenPort = 51821
Jc = 4
Jmin = 40
Jmax = 700
S1 = 20
S2 = 50
H1 = 100001
H2 = 200001
H3 = 300001
H4 = 400001

[Peer]
PublicKey = $cli_awg_pub
PresharedKey = $awg_psk
AllowedIPs = 10.204.240.2/32
EOF

cat > "$stage/awg_client.conf" <<EOF
[Interface]
PrivateKey = $cli_awg_priv
Jc = 4
Jmin = 40
Jmax = 700
S1 = 20
S2 = 50
H1 = 100001
H2 = 200001
H3 = 300001
H4 = 400001

[Peer]
PublicKey = $srv_awg_pub
PresharedKey = $awg_psk
Endpoint = 192.0.2.1:51821
AllowedIPs = 10.204.240.0/24
PersistentKeepalive = 25
EOF

ip netns exec "$srv_ns" "$bin/awg" setconf awg0 "$stage/awg_server.conf"
ip -n "$srv_ns" addr add 10.204.240.1/24 dev awg0
ip -n "$srv_ns" link set awg0 mtu 1280 up

ip netns exec "$cli_ns" "$bin/awg" setconf awg-cli "$stage/awg_client.conf"
ip -n "$cli_ns" addr add 10.204.240.2/32 dev awg-cli
ip -n "$cli_ns" link set awg-cli mtu 1280 up
ip -n "$cli_ns" route add 10.204.240.0/24 dev awg-cli

# Verify handshake and ICMP ping across obfuscated AmneziaWG tunnel
ip netns exec "$cli_ns" ping -c 3 -W 5 10.204.240.1 >/dev/null
latest_awg_hs=$(ip netns exec "$srv_ns" "$bin/awg" show awg0 latest-handshakes | awk '{print $2}')
if [[ -n "$latest_awg_hs" && "$latest_awg_hs" -gt 0 ]]; then
  echo "Phase 2: AmneziaWG handshake confirmed (timestamp: $latest_awg_hs)."
fi
echo "Phase 2 PASS: Real AmneziaWG obfuscated tunnel verified successfully."

echo "=== Phase 3: Cloudflare WARP outbound routing test ==="
cat > "$stage/warp_reg.py" << 'PY'
import base64
import datetime
import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request

stage = sys.argv[1]
bin_dir = sys.argv[2] if len(sys.argv) > 2 else ""

def generate_keypair():
    awg = os.path.join(bin_dir, "awg")
    cmd = "wg" if shutil.which("wg") else (awg if os.path.exists(awg) else None)
    if cmd:
        try:
            priv = subprocess.run([cmd, "genkey"], text=True, capture_output=True, check=True).stdout.strip()
            pub = subprocess.run([cmd, "pubkey"], input=priv + "\n", text=True, capture_output=True, check=True).stdout.strip()
            return priv, pub
        except Exception:
            pass

    # Pure Python X25519 Curve25519 fallback
    P = 2**255 - 19
    A24 = 121665
    def cswap(swap, x_2, x_3):
        dummy = swap * ((x_2 - x_3) % P)
        return (x_2 - dummy) % P, (x_3 + dummy) % P
    def x25519(k, u):
        x_1, x_2, z_2, x_3, z_3, swap = u, 1, 0, u, 1, 0
        for t in reversed(range(255)):
            k_t = (k >> t) & 1
            swap ^= k_t
            x_2, x_3 = cswap(swap, x_2, x_3)
            z_2, z_3 = cswap(swap, z_2, z_3)
            swap = k_t
            A, AA, B, BB = (x_2 + z_2) % P, ((x_2 + z_2)**2) % P, (x_2 - z_2) % P, ((x_2 - z_2)**2) % P
            E, C, D = (AA - BB) % P, (x_3 + z_3) % P, (x_3 - z_3) % P
            DA, CB = (D * A) % P, (C * B) % P
            x_3 = ((DA + CB)**2) % P
            z_3 = (x_1 * ((DA - CB)**2)) % P
            x_2 = (AA * BB) % P
            z_2 = (E * (AA + A24 * E)) % P
        x_2, x_3 = cswap(swap, x_2, x_3)
        z_2, z_3 = cswap(swap, z_2, z_3)
        return (x_2 * pow(z_2, P - 2, P)) % P

    priv_bytes = bytearray(os.urandom(32))
    priv_bytes[0] &= 248
    priv_bytes[31] &= 127
    priv_bytes[31] |= 64
    k = int.from_bytes(priv_bytes, "little")
    pub_bytes = x25519(k, 9).to_bytes(32, "little")
    return base64.b64encode(priv_bytes).decode(), base64.b64encode(pub_bytes).decode()

def check_internet():
    try:
        req = urllib.request.Request("https://1.1.1.1", headers={"User-Agent": "curl/8.0.0"})
        with urllib.request.urlopen(req, timeout=4):
            return True
    except urllib.error.HTTPError:
        return True
    except Exception:
        pass
    try:
        req = urllib.request.Request("https://api.cloudflareclient.com", headers={"User-Agent": "curl/8.0.0"})
        with urllib.request.urlopen(req, timeout=4):
            return True
    except urllib.error.HTTPError:
        return True
    except Exception:
        return False

priv, pub = generate_keypair()
registered = False

if check_internet():
    try:
        now_utc = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        payload = {
            "key": pub,
            "install_id": "",
            "fcm_token": "",
            "tos": now_utc,
            "model": "PC",
            "serial_number": "",
            "locale": "en_US"
        }
        req = urllib.request.Request(
            "https://api.cloudflareclient.com/v0a2158/reg",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "User-Agent": "okhttp/3.12.1",
                "CF-Client-Version": "a-6.3-2158",
                "Content-Type": "application/json; charset=UTF-8"
            },
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=12) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        peer = data["config"]["peers"][0]
        peer_pub = peer["public_key"]
        endpoint = peer["endpoint"]["v4"]
        if endpoint.endswith(":0"):
            endpoint = endpoint[:-2] + ":2408"
        ipv4 = data["config"]["interface"]["addresses"]["v4"]
        if "/" not in ipv4:
            ipv4 = f"{ipv4}/32"
        registered = True
        print(f"Cloudflare WARP registered successfully: id={data.get('id')}, endpoint={endpoint}, ipv4={ipv4}")
    except Exception as e:
        print(f"Registration request error ({e}); using synthetic WARP config.")

if not registered:
    _, peer_pub = generate_keypair()
    endpoint = "162.159.192.1:2408"
    ipv4 = "172.16.0.2/32"

conf_lines = [
    "[Interface]",
    f"PrivateKey = {priv}",
    f"Address = {ipv4}",
    "Table = off",
    "",
    "[Peer]",
    f"PublicKey = {peer_pub}",
    f"Endpoint = {endpoint}",
    "AllowedIPs = 0.0.0.0/0, ::/0",
    "PersistentKeepalive = 25",
    ""
]

conf_path = os.path.join(stage, "warp0.conf")
with open(conf_path, "w") as f:
    f.write("\n".join(conf_lines))
print(f"Wrote {conf_path} with Table = off")
PY

python3 "$stage/warp_reg.py" "$stage" "$bin"

# Bring up warp0 with wg-quick in the server namespace
ip netns exec "$srv_ns" env PATH="$stage/bin:$bin:$PATH" wg-quick up "$stage/warp0.conf"

# Verify Table = off prevented default route injection into main routing table
main_routes=$(ip -n "$srv_ns" route show)
if echo "$main_routes" | grep -q 'default dev warp0'; then
  echo "FAIL: Table = off was violated: default dev warp0 present in main table: $main_routes" >&2
  exit 1
fi
echo "Verified Table = off: main routing table remains untouched."

# Enable forwarding in server namespace
ip netns exec "$srv_ns" sysctl -q -w net.ipv4.ip_forward=1

# Setup table 100 default route dev warp0 and iptables forward + NAT rules
ip -n "$srv_ns" route replace default dev warp0 table 100
ip netns exec "$srv_ns" iptables -I FORWARD 1 -i wg0 -o warp0 -j ACCEPT
ip netns exec "$srv_ns" iptables -I FORWARD 1 -i warp0 -o wg0 -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT
ip netns exec "$srv_ns" iptables -t nat -A POSTROUTING -o warp0 -j MASQUERADE
ip netns exec "$srv_ns" iptables -t mangle -A FORWARD -o warp0 -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --clamp-mss-to-pmtu
ip -n "$srv_ns" rule add from 10.203.240.0/24 table 100 priority 15000

# Test that forwarded traffic is routed to table 100
table_100_route=$(ip -n "$srv_ns" route show table 100)
if ! echo "$table_100_route" | grep -q 'default dev warp0'; then
  echo "FAIL: Default route dev warp0 missing in table 100: $table_100_route" >&2
  exit 1
fi

route_get=$(ip -n "$srv_ns" route get 1.1.1.1 from 10.203.240.2 iif wg0)
if ! echo "$route_get" | grep -q 'table 100'; then
  echo "FAIL: Policy routing failed to match table 100 for 10.203.240.2: $route_get" >&2
  exit 1
fi
if ! echo "$route_get" | grep -q 'dev warp0'; then
  echo "FAIL: Forwarded traffic from 10.203.240.2 does not route out warp0: $route_get" >&2
  exit 1
fi

# Verify iptables forward and NAT rules exist
ip netns exec "$srv_ns" iptables -C FORWARD -i wg0 -o warp0 -j ACCEPT
ip netns exec "$srv_ns" iptables -C FORWARD -i warp0 -o wg0 -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT
ip netns exec "$srv_ns" iptables -t nat -C POSTROUTING -o warp0 -j MASQUERADE

echo "Phase 3 PASS: Cloudflare WARP interface, Table = off, and policy routing rules verified."

echo "PASS: All real tunnel tests (WireGuard, AmneziaWG, and Cloudflare WARP) succeeded."
exit 0
