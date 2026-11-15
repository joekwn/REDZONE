# Putting REDZONE online (no domain needed)

There are two ways. Pick one.

| | **A. Free cloud server** (recommended) | **B. Cloudflare quick tunnel** |
|---|---|---|
| Good for | the real public site | a quick demo (judges, teammates) |
| Runs on | an Oracle Cloud "Always Free" server, 24/7 | your own laptop, only while it is on |
| Address | `https://<your-ip>.sslip.io` (free, automatic) | a random `https://xxxx.trycloudflare.com` |
| Cost | free (Oracle asks for a card to check who you are) | free, no account |
| Time | about 45 minutes the first time | 5 minutes |

You never need to buy a domain. If you get one later (or a free DuckDNS name),
you just run the setup script again with that name.

---

## A. Free cloud server (Oracle Cloud Always Free)

### A1. Create the server

1. Sign up at **cloud.oracle.com** (choose "Free Tier"). Pick a home region near you;
   it cannot be changed later.
2. Menu → **Compute → Instances → Create instance**.
   - **Image:** Canonical **Ubuntu 24.04** (or 22.04).
   - **Shape:** *Ampere* **VM.Standard.A1.Flex**, 2 OCPU and 12 GB memory
     (inside the free allowance). If A1 says "out of capacity", try again later
     or use **VM.Standard.E2.1.Micro** (works, but slower).
   - **Boot volume:** 100 GB is plenty (the free allowance is 200 GB in total).
   - **SSH keys:** choose "Generate a key pair for me" and **download the private key**
     (for example `redzone.key`). Keep it safe; it is the only way to log in.
3. Click **Create**. When it is running, copy the **Public IP address** (for example `140.238.1.2`).

### A2. Open the web ports in Oracle's network firewall

Oracle has two firewalls. The setup script opens the one inside the server.
This one you open by hand, once:

Instance page → **Subnet** link → **Security Lists** → *Default Security List* →
**Add Ingress Rules**, twice:

| Source CIDR | IP protocol | Destination port |
|---|---|---|
| `0.0.0.0/0` | TCP | `80` |
| `0.0.0.0/0` | TCP | `443` |

### A3. Make the upload package on Windows

In PowerShell, in `C:\Users\ASUS\REDZONE`, **with the website server stopped** (Ctrl+C):

```powershell
powershell -ExecutionPolicy Bypass -File deploy\make_release.ps1
```

This makes `redzone_release.zip` with the code and `data\redzone.duckdb` only
(not the raw CSV and Parquet files, which the website does not need).

### A4. Upload it and log in

Windows 10/11 already has `scp` and `ssh`. Replace the IP with yours:

```powershell
scp -i $HOME\Downloads\redzone.key redzone_release.zip ubuntu@140.238.1.2:~
ssh -i $HOME\Downloads\redzone.key ubuntu@140.238.1.2
```

If Windows complains that the key's permissions are "too open":

```powershell
icacls $HOME\Downloads\redzone.key /inheritance:r /grant:r "$($env:USERNAME):R"
```

### A5. Run the setup (on the server)

```bash
sudo apt-get update && sudo apt-get install -y unzip
unzip -o redzone_release.zip -d redzone_release
cd redzone_release
sudo bash deploy/setup.sh
```

At the end it prints your address, for example **https://140-238-1-2.sslip.io**.
sslip.io is a free service that turns any IP into a hostname, so the site
can get a real HTTPS certificate without a domain.

### A6. Add your keys (never put them in code or chat)

```bash
sudo nano /etc/redzone/redzone.env
```

Fill in:

- `FIRMS_MAP_KEY=` your NASA FIRMS key
- `REDZONE_ADMIN_EMAILS=` the email you will register with on the site
- `REDZONE_CONTACT_EMAIL=` an address people can write to (shown on the Privacy and Terms pages)

Save (Ctrl+O, Enter, Ctrl+X), then:

```bash
sudo systemctl restart redzone
```

Open your address and **register** with the admin email. The live-update
status should no longer say "Off — set FIRMS_MAP_KEY", and the dot next to it turns green.

### A7. Everyday commands

| What | Command |
|---|---|
| Is it running? | `sudo systemctl status redzone` |
| Live log | `sudo journalctl -u redzone -f` |
| Restart | `sudo systemctl restart redzone` |
| Backup now | `sudo bash /opt/redzone/deploy/backup.sh` |
| List backups | `ls -lh /opt/redzone/backups` |
| Health check | `curl http://127.0.0.1:8000/healthz` |

Backups run every day at 03:30 UTC and are kept for 14 days.

**Install a newer version:** make a new `redzone_release.zip`, upload it the same way, then

```bash
unzip -o redzone_release.zip -d redzone_new && cd redzone_new && sudo bash deploy/update.sh
```

It backs up first and keeps your database, accounts and settings.

**Restore a backup:**

```bash
sudo systemctl stop redzone
sudo bash -c 'gunzip -c /opt/redzone/backups/redzone_YYYYMMDD_HHMM.duckdb.gz > /opt/redzone/data/redzone.duckdb'
sudo chown redzone:redzone /opt/redzone/data/redzone.duckdb
sudo systemctl start redzone
```

### A8. A nicer free name (optional)

1. Go to **duckdns.org**, sign in, create a name such as `redzone-fires`, and set its IP to your server's IP.
2. On the server, from the release folder: `sudo bash deploy/setup.sh redzone-fires.duckdns.org`

The same works later with a paid domain: point its **A record** to the server IP and run the setup with that name.

---

## B. Cloudflare quick tunnel (demo from your laptop)

1. Download `cloudflared` for Windows:
   `winget install --id Cloudflare.cloudflared`
2. In one PowerShell window, start REDZONE as usual (`py -m uvicorn webapp.server:app --port 8000`).
3. In a second window: `cloudflared tunnel --url http://localhost:8000`
4. It prints an address like `https://random-words.trycloudflare.com`. Share it.

For accounts to work properly over the tunnel, stop the server, then in the first window:

```powershell
$env:REDZONE_BASE_URL = "https://random-words.trycloudflare.com"   # the address from step 4
$env:REDZONE_TRUST_PROXY = "1"
py -m uvicorn webapp.server:app --port 8000
```

Limits: the address changes every time you start the tunnel, and the site is
offline when your laptop sleeps. Use it for demos, not for the public site.

---

## Before you announce it: checklist

- [ ] The site opens with a padlock (HTTPS) at your address.
- [ ] `/privacy` and `/terms` show your contact email (not "contact address not set").
- [ ] You registered with the admin email and can see the admin tools in the **Data** tab.
- [ ] The live-update status does not say "Off — set FIRMS_MAP_KEY", and **Update now** works.
- [ ] A test look-up in the **Look up** tab works.
- [ ] `sudo bash /opt/redzone/deploy/backup.sh` makes a file in `/opt/redzone/backups`.
- [ ] Reboot test: `sudo reboot`, wait 2 minutes, the site comes back by itself.
- [ ] Keep these private: the SSH key, `/etc/redzone/redzone.env`, `data/secret.key`.

## What the setup builds

```
Internet ──HTTPS──► Caddy (ports 80/443, automatic certificate)
                       │
                       ▼
               REDZONE (uvicorn, 127.0.0.1:8000, one worker)
                       │
                       ▼
           /opt/redzone/data/redzone.duckdb ──► daily backup, 14 days
```

- REDZONE runs as its own user `redzone`, with no login and no access to other folders.
- It is only reachable through Caddy; port 8000 is not open to the internet.
- One worker only: DuckDB allows one process to write at a time.
- If it crashes, systemd restarts it within 5 seconds; it also starts on boot.
