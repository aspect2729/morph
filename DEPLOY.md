# Put Morph online for free — Oracle Cloud Always Free

You'll end up with Morph at `https://yourname.duckdns.org`, running 24/7 on a free
2-CPU / 12 GB server, with automatic HTTPS. Total cost: ₹0. Time: about 45 minutes,
most of it waiting.

What you need: a card for Oracle's identity check (not charged on Always Free), and this Mac.

---

## 1. Create your Oracle Cloud account (10 min)

1. Go to **https://cloud.oracle.com/free** → **Start for free**.
2. Choose your **Home Region** carefully — pick one near you (e.g. **India South (Hyderabad)** or
   **India West (Mumbai)**). It can't be changed later, and free servers only exist there.
3. Finish the card verification. You stay on **Always Free** unless you choose to upgrade.

## 2. Create the free server (10 min)

1. In the Oracle console: **☰ → Compute → Instances → Create instance**.
2. **Name:** `morph`
3. **Image and shape → Edit:**
   - **Image:** *Canonical Ubuntu 24.04* (it picks the ARM version automatically).
   - **Shape → Change shape → Ampere → VM.Standard.A1.Flex**, set **2 OCPUs** and **12 GB memory**.
     The "Always Free-eligible" tag must show.
4. **Networking:** keep *Create new virtual cloud network* and *Assign a public IPv4 address*.
5. **Add SSH keys:** choose **Generate a key pair for me** and click **Save private key**.
   Keep that file safe (e.g. `~/Downloads/ssh-key-morph.key`).
6. **Boot volume:** leave the default (50 GB, free).
7. Click **Create**. When it says **Running**, copy the **Public IP address**.

> **"Out of capacity for shape VM.Standard.A1.Flex"?** That's normal for free servers in busy regions.
> Try another *Availability domain* on the same page, or try again a few hours later.

## 3. Open the web ports in Oracle's firewall (3 min)

1. On the instance page, click the **Subnet** link → **Security** (or *Security Lists*) → the **Default Security List**.
2. **Add Ingress Rules** — add two rules:
   - Source CIDR `0.0.0.0/0`, IP Protocol **TCP**, Destination port **80**
   - Source CIDR `0.0.0.0/0`, IP Protocol **TCP**, Destination port **443**

## 4. Get a free domain name (3 min)

1. Go to **https://www.duckdns.org** and sign in (Google/GitHub).
2. Type a name (e.g. `mymorph`) → **add domain**.
3. In **current ip**, paste your server's **Public IP** → **update ip**.

Your address is now `mymorph.duckdns.org`.

## 5. Send Morph to the server (5 min, from this Mac)

In Terminal on the Mac:

```bash
chmod 600 ~/Downloads/ssh-key-morph.key
```

```bash
~/Morph/deploy/push.sh ubuntu@YOUR_PUBLIC_IP ~/Downloads/ssh-key-morph.key
```

## 6. Start it on the server (15 min, mostly the first build)

Log in to the server:

```bash
ssh -i ~/Downloads/ssh-key-morph.key ubuntu@YOUR_PUBLIC_IP
```

Then, on the server:

```bash
cd ~/morph && bash deploy/setup-server.sh
```

```bash
nano .env
```

Set `DOMAIN=mymorph.duckdns.org` (your name), save with **Ctrl+O, Enter, Ctrl+X**.
Log out (`exit`) and back in with the same `ssh` command, then:

```bash
cd ~/morph && docker compose up -d --build
```

The first build downloads LibreOffice and friends (~10–15 min). When it finishes, check everything:

```bash
docker compose exec morph python scripts/smoke_test.py http://127.0.0.1:8000 --web
```

It must end with **All checks passed.** Then open **https://mymorph.duckdns.org** — done.

---

## Updating Morph later

On the Mac, after changes:

```bash
~/Morph/deploy/push.sh ubuntu@YOUR_PUBLIC_IP ~/Downloads/ssh-key-morph.key
```

On the server:

```bash
cd ~/morph && docker compose up -d --build && docker compose exec morph python scripts/smoke_test.py http://127.0.0.1:8000 --web
```

If the smoke test fails, the previous image is still on the server; tell Claude what failed.

## Useful commands (on the server)

| What | Command |
|---|---|
| Is it running? | `docker compose ps` |
| Live logs | `docker compose logs -f morph` |
| Restart | `docker compose restart morph` |
| Stop | `docker compose down` |
| Disk usage | `docker system df` |

## What's protected

- Every visitor gets a **private workspace** (a random cookie). Nobody can see, download or open
  anyone else's uploads, conversions, editing sessions or Recent files — this is checked by the smoke test.
- Uploads over **100 MB**, documents over **300 pages** and transcriptions over **20 minutes** are refused.
- **60 heavy requests per minute** per visitor; at most **3 conversions at once** per visitor, **2** server-wide.
- Converted files are deleted after **2 hours**; unused workspaces after **14 days**.
- HTTPS with HSTS, strict Content-Security-Policy, no frames, no sniffing; the app runs as a non-root user.
- Docker restarts Morph automatically if it ever stops answering its health check.

All limits are in `.env` (see `.env.example`).

## Good to know

- Oracle may **reclaim Always Free servers that sit idle** for a long time. Visitors using Morph keep it
  active. Upgrading the account to *Pay As You Go* stops reclaiming, and you're still not charged while you
  stay within the free limits.
- DuckDNS needs no renewal; if your server's IP ever changes, update it on duckdns.org.
- Adding sign-in (Supabase) later doesn't change any of this — it adds accounts on top.
