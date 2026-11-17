# Self-hosting on an Ubuntu laptop (free)

Run the server on a spare Ubuntu / Linux Mint laptop and make it reachable at
`https://signs.signbridgeco.xyz` through a free Cloudflare Tunnel. No router
ports are opened and no static IP is needed.

```text
Browser / app ──HTTPS──▶ Cloudflare (free) ──Tunnel──▶ Ubuntu laptop: 127.0.0.1:8000
                                                        └─ /var/lib/personal-signs (clips, models)
```

## 1. Prepare the laptop

1. `sudo apt update && sudo apt upgrade -y`
2. Stop it from sleeping (Cinnamon: **System Settings → Power Management**):
   *Suspend when inactive* → **Never** (on AC power); *When the lid is closed* → **Do nothing**.
3. Keep it plugged in, ideally on a cable network.

## 2. Install the server

```bash
sudo apt install -y git
git clone https://github.com/Lukesh2006/isl-personal-signs.git ~/isl-personal-signs
sudo bash ~/isl-personal-signs/deploy/ubuntu/install.sh
```

This installs the code to `/opt/personal-signs`, downloads the MediaPipe model
files, creates a `personal-signs` service user, generates an **API key** in
`/etc/personal-signs/personal-signs.env`, and starts a service that also starts
after every reboot. Check it:

```bash
curl http://127.0.0.1:8000/health
sudo grep API_KEY /etc/personal-signs/personal-signs.env   # the key clients must send
```

## 3. Cloudflare Tunnel

The domain's DNS must be on Cloudflare (free plan): add the domain at
<https://dash.cloudflare.com>, then change the registrar's nameservers to the
two Cloudflare shows. Keep website records "DNS only" if the site is hosted
elsewhere (for example on Vercel).

```bash
# Install cloudflared from Cloudflare's package repository
sudo mkdir -p --mode=0755 /usr/share/keyrings
curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg | sudo tee /usr/share/keyrings/cloudflare-main.gpg >/dev/null
echo 'deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared any main' | sudo tee /etc/apt/sources.list.d/cloudflared.list
sudo apt update && sudo apt install -y cloudflared

# Log in (browser opens), create the tunnel and its DNS name
cloudflared tunnel login
cloudflared tunnel create personal-signs          # prints the TUNNEL_ID
cloudflared tunnel route dns personal-signs signs.signbridgeco.xyz

# Run it as a service
sudo mkdir -p /etc/cloudflared
sudo cp ~/.cloudflared/<TUNNEL_ID>.json /etc/cloudflared/
sudo cp /opt/personal-signs/deploy/ubuntu/cloudflared-config.yml.example /etc/cloudflared/config.yml
sudo nano /etc/cloudflared/config.yml             # replace TUNNEL_ID in both places
sudo cloudflared service install
sudo systemctl enable --now cloudflared
```

From any device: `curl https://signs.signbridgeco.xyz/health`, then open
`https://signs.signbridgeco.xyz` and enter the API key under **Settings**.

## Everyday commands

| Task | Command |
|---|---|
| Update to the latest version | `sudo bash /opt/personal-signs/deploy/ubuntu/install.sh` |
| Server logs | `sudo journalctl -u personal-signs -f` |
| Tunnel logs | `sudo journalctl -u cloudflared -f` |
| Restart | `sudo systemctl restart personal-signs` |
| Back up recordings | `sudo tar czf personal-signs-data.tgz -C /var/lib personal-signs/data` |

## Security notes

- The server listens on `127.0.0.1` only; the tunnel is the only way in.
- Every API call needs the API key; the key file is readable only by root and the service.
- The service can write only to `/var/lib/personal-signs`.
- Recordings are personal data: back them up privately and delete a person's
  folder under `data/training_samples/` if they ask.
