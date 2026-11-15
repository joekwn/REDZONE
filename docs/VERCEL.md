# Putting REDZONE on Vercel

Vercel runs the website but cannot keep files or keep programs running. So:

| Part | Where it lives on the Vercel setup |
|---|---|
| Website (map, accounts, look-ups) | **Vercel** (free Hobby plan), built from your GitHub repo |
| Database (fires, accounts, sites, events) | **MotherDuck**, a cloud DuckDB (free plan: 10 GB storage, 10 compute hours a month) |
| New FIRMS data every 3 h and event checks every hour | **GitHub Actions** calls the website on a schedule (Vercel's free plan allows only one run a day) |
| Collecting past data | **Your computer**, writing straight into MotherDuck |

---

## 1. MotherDuck: put the database in the cloud

1. Sign up at **motherduck.com** (free plan, no card needed).
2. Settings → **Access Tokens** → create a token. Copy it. Never put it in code, chat or GitHub.
3. In PowerShell, in `C:\Users\ASUS\REDZONE`, with the website server **stopped**:

```powershell
py -c "import duckdb; print(duckdb.__version__)"
```

MotherDuck accepts DuckDB **1.4.1 to 1.5.5**. If yours is different:

```powershell
py -m pip install "duckdb==1.5.5"
```

4. Upload:

```powershell
$env:MOTHERDUCK_TOKEN = "paste-your-token-here"
py scripts\upload_to_motherduck.py
```

It ends with `DONE. On Vercel set: REDZONE_DB = md:redzone_cloud` and both row counts should match.

## 2. Make two secrets for the website

Run this twice and keep both lines somewhere private (for example a password manager):

```powershell
py -c "import secrets; print(secrets.token_hex(32))"
```

The first is your **REDZONE_SECRET**, the second your **CRON_SECRET**.

## 3. Vercel: create the project

On the "New Project" page (Import from GitHub → joekwn/REDZONE):

- **Root Directory:** `./`
- **Application Preset:** FastAPI
- **Build and Output Settings:** leave as they are.
- **Environment Variables** (click "Add More" for each):

| Key | Value |
|---|---|
| `REDZONE_DB` | `md:redzone_cloud` |
| `MOTHERDUCK_TOKEN` | your MotherDuck token |
| `FIRMS_MAP_KEY` | your NASA FIRMS key |
| `REDZONE_SECRET` | secret 1 from step 2 |
| `CRON_SECRET` | secret 2 from step 2 |
| `REDZONE_ADMIN_EMAILS` | the email you will register with |
| `REDZONE_CONTACT_EMAIL` | an address people can write to |
| `REDZONE_TRUST_PROXY` | `1` |

Click **Deploy**. When it finishes, copy your address, e.g. `https://redzone-abc.vercel.app`.
Then add one more variable (Project → **Settings → Environment Variables**):

| Key | Value |
|---|---|
| `REDZONE_BASE_URL` | your address, e.g. `https://redzone-abc.vercel.app` |

and redeploy (**Deployments** → the latest one → ⋯ → **Redeploy**).

## 4. GitHub Actions: the 3-hour schedule

On github.com → your repo → **Settings → Secrets and variables → Actions → New repository secret**, add:

| Name | Value |
|---|---|
| `REDZONE_URL` | your Vercel address |
| `CRON_SECRET` | the same secret 2 |

Then **Actions** tab → "REDZONE schedule" → **Run workflow**. A green tick means the update worked.
After that it runs by itself every 3 hours (updates) and every hour (event checks).
GitHub may start scheduled runs a few minutes late; that is normal.

## 5. Check

- Open your address: welcome page, then the map.
- Register with the admin email. The live-update line should show the last update time.
- `/privacy` and `/terms` show your contact email.
- `/healthz` shows `{"ok":true}`.

## Past data (from your computer)

The website cannot run long jobs on Vercel, so the "Collect" button explains this.
Run it from PowerShell instead; it writes straight into MotherDuck:

```powershell
$env:REDZONE_DB = "md:redzone_cloud"
$env:MOTHERDUCK_TOKEN = "your-token"
$env:FIRMS_MAP_KEY = "your-firms-key"
py scripts\firms_backfill.py --start 2025-10-01 --end 2025-10-31
```

Close the window afterwards (the variables only live in that window). Without
`REDZONE_DB`, the scripts use your local `data\redzone.duckdb` as before.

## Updating the site

Edit code → `git add .` → `git status` → `git commit -m "..."` → `git push`. Vercel redeploys by itself.

## Limits to know (free plans)

- **MotherDuck compute: 10 hours a month.** Each map refresh is a query on 21 million rows.
  For a demo or judging this is plenty; with many daily users it can run out. Watch it in
  MotherDuck → Settings → Usage.
- **First visit after a quiet period is slower** (a few seconds) while Vercel starts the app
  and connects to MotherDuck.
- **Rate limits for look-ups and sign-in attempts** are counted per running copy of the app,
  so they are looser than on a normal server.
- **Email features stay off** (no SMTP), as on the local version.
