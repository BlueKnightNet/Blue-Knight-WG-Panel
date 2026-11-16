# Blue Knight WG Panel

A web panel for WireGuard and AmneziaWG on your own VPS.

- Create, pause, rename and revoke devices.
- Download configs, QR codes, or a ZIP with every DNS preset.
- View recent connections and traffic usage.
- Customize DNS, connection profiles and themes.

## Install step by step

### 1. Prepare your VPS

Use **Ubuntu or Debian with systemd**, root/sudo access and a public IPv4 address. Restricted bot hosting containers are unsupported.

Connect to the server:

```bash
ssh root@YOUR_VPS_IP
```

### 2. Install the panel

```bash
sudo apt-get update && sudo apt-get install -y curl ca-certificates
curl -fsSL https://raw.githubusercontent.com/BlueKnightNet/Blue-Knight-WG-Panel/blue-knight-net/install.sh -o /tmp/wg-auto-install.sh
sudo bash /tmp/wg-auto-install.sh
```

### 3. Open the ports

In your VPS provider's firewall, allow **51820/UDP** for WireGuard and **8787/TCP** for the panel. Keep your SSH port open.

### 4. Sign in

Open `https://YOUR_VPS_IP:8787`. The panel uses a self-signed certificate, so your browser will show a certificate warning.

Username: **admin**. Display your generated password on the VPS:

```bash
sudo cat /etc/wireguard/wg-auto-panel/password
```

You can change it in **Settings**.

### 5. Connect a device

1. Create a device in the panel.
2. Download its `.conf` or scan its QR code in the WireGuard app.
3. Activate the tunnel and test internet access.
4. Use **All DNS configs ZIP** to download DNS variants. Their filenames include the DNS preset name; activate only one variant at a time.

Create a separate device for each phone or computer. Recent handshakes show active configs; they cannot count people sharing one key. Traffic counters can reset when the VPN restarts.

## Optional: AmneziaWG

1. Select **AmneziaWG** in the sidebar.
2. Click **Install AmneziaWG** and wait for the build to finish.
3. Open its displayed UDP port in your provider firewall.
4. Create a device and import its config into an **AmneziaWG compatible app**.

AmneziaWG uses separate keys and settings. Its configs require a compatible client.

## Docker Deployment (GHCR)

Multi-architecture Docker images (`linux/amd64` and `linux/arm64`) are published automatically on merge:

```bash
docker run -d \
  --name blue-knight-panel \
  --restart unless-stopped \
  --cap-add=NET_ADMIN \
  --cap-add=NET_RAW \
  -v /dev/net/tun:/dev/net/tun \
  -v /etc/wireguard:/etc/wireguard \
  -v /etc/amnezia/amneziawg:/etc/amnezia/amneziawg \
  -v /opt/wg-auto:/opt/wg-auto \
  -p 8787:8787/tcp \
  -p 51820:51820/udp \
  -p 51821:51821/udp \
  ghcr.io/blueknightnet/blue-knight-wg-panel:latest
```

### AmneziaWG Kernel Module vs Userspace Fallback
By default, the panel and container prioritize the host system's native **`amneziawg` kernel module** if available (recommended for maximum throughput and low CPU usage). If the host kernel module is not present, it automatically and seamlessly falls back to the built-in **`amneziawg-go`** userspace engine.

To install the native kernel module on your host VPS, follow the official instructions at [amnezia-vpn/amneziawg-linux-kernel-module](https://github.com/amnezia-vpn/amneziawg-linux-kernel-module):
- **Ubuntu**:
  ```bash
  sudo add-apt-repository -y ppa:amnezia/ppa
  sudo apt-get update
  sudo apt-get install -y amneziawg amneziawg-dkms
  ```
- **Debian**: Follow the Launchpad repository setup in the [kernel module repository](https://github.com/amnezia-vpn/amneziawg-linux-kernel-module#debian).
- When using `install.sh`, the installer will also interactively offer to install the kernel module for you.

## Update

For an existing installation, switch its updater to this repository once:

```bash
curl -fsSL https://raw.githubusercontent.com/BlueKnightNet/Blue-Knight-WG-Panel/blue-knight-net/update.sh -o /tmp/wg-auto-update.sh
sudo bash /tmp/wg-auto-update.sh --install
```

After that, use the panel's update button or:

```bash
sudo wg-auto-update
```

Existing clients, keys and settings are preserved.

## Troubleshooting

```bash
sudo systemctl status wg-auto-panel --no-pager
sudo wg show wgauto0
sudo journalctl -u wg-auto-panel -n 50 --no-pager
```

If a device connects without internet, run **Check VPS networking** in the panel and check your provider firewall. DNS changes alone cannot fix an unreachable VPN server.

## Development

```bash
python build_panel.py
python -m unittest -q test_panel test_local_app
```

UI sources are in `web/`; rebuild `panel.html` after editing them. See [UPSTREAM.md](UPSTREAM.md) for project background.

## Device limits

Open a device?s **Data limit & expiry** section to set a combined upload/download quota in GiB and an expiry date in your browser?s local time. Zero quota means unlimited; empty expiry means never. Usage is persisted and limits are checked every five seconds while the panel service runs, even with the browser closed. Exhausted or expired keys are removed from the live VPN. Extend the limit or reset usage to restore access; manual pauses remain paused. Small overshoot is possible between checks. Counters cannot recover traffic already lost before this version was installed or during a service outage.
