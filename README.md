# Lavish Shawarma — Billing, Money & Stock

A website for the shop that:

- **Bills fast** on phone, tablet or laptop (tap items, sizes, extras, discount, cash/UPI/card/online, token number, thermal receipt or PDF).
- **Tracks every rupee**: sales, money received by method, unpaid bills, expenses, and a **daily cash-drawer check** (counted vs. expected).
- **Tracks chicken, rotis and other stock**: every bill automatically uses up stock by recipe; you record purchases, wastage and staff meals; at closing you count what's left and the app shows **what's missing and what it's worth in ₹**.
- **Plans tomorrow's prep** (how much chicken / how many rotis) from past sales.
- **Syncs live**: an order on one device shows on every other device within about 4 seconds.
- Owner + staff logins, staff limits, approvals for cancellations/discounts, and a full **audit log** of every change.
- Works as an **installable app** on phones (PWA), with dark/light mode.

Built with **Python (Django)** + **PostgreSQL on Supabase**, hosted on **Render**, domain on **Cloudflare**.

---

## What it costs

| Item | Cost per year |
|---|---|
| Domain from Cloudflare (`.com`, same price on renewal) | about $10.46 (≈ ₹900–950) |
| Supabase database (free plan, 500 MB) | ₹0 |
| Render web hosting (free plan) | ₹0 |
| Nightly backups (GitHub Actions) | ₹0 |
| Keep-awake pinger (UptimeRobot free) | ₹0 |
| **Total** | **≈ ₹1,000 / year** |

Cloudflare doesn't sell `.in` domains. If you specifically want `.in`, buy it from another registrar and still use Cloudflare for DNS (step 5 works the same).

---

## The words used below, in plain English

| Word | Meaning |
|---|---|
| **venv** (virtual environment) | A private folder of Python libraries just for this project, so it never clashes with anything else on your laptop. |
| **Git / GitHub** | Git saves versions of your code. GitHub stores them online. Render reads your code from GitHub. |
| **Supabase** | The cloud PostgreSQL database where all orders, money and stock live. |
| **Render** | The server that runs the website 24×7. |
| **Cloudflare** | Where you buy the domain name and point it at Render. |
| **`.env` file** | A private settings file with passwords. It **never** goes to GitHub. |
| **migrate** | Creates or updates the database tables. |

---

## Step 1 — Run it on your laptop

You need **Python 3.12 or newer** ([python.org/downloads](https://www.python.org/downloads/); on Windows tick **“Add Python to PATH”**) and **Git** ([git-scm.com](https://git-scm.com/downloads)).

Open a terminal (Windows: **PowerShell**; Mac: **Terminal**) inside the project folder (the one containing `manage.py`).

**1. Create and switch on the venv**

```powershell
# Windows
python -m venv .venv
.venv\Scripts\activate
```
```bash
# Mac / Linux
python3 -m venv .venv
source .venv/bin/activate
```
You'll see `(.venv)` at the start of the line. **Every time** you open a new terminal for this project, run the `activate` line again.

> If Windows says *“running scripts is disabled”*, run this once:
> `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` and press Y.

**2. Install the libraries**

```bash
pip install -r requirements.txt
```

**3. Create your private settings file**

```powershell
copy .env.example .env      # Windows
```
```bash
cp .env.example .env        # Mac / Linux
```
Open `.env` in any editor and set `OWNER_PASSWORD=` to a password of at least 8 characters (letters and numbers, e.g. `Lavish2026Shop`). Leave `DATABASE_URL` empty for now. This uses a local test database file.

**4. Create the database, load the menu, create the owner login**

```bash
python manage.py migrate
python manage.py seed_shop
python manage.py ensure_owner
```

**5. Start it**

```bash
python manage.py runserver
```
Open **http://127.0.0.1:8000** and log in as **owner** with your password. Press `Ctrl + C` in the terminal to stop it.

**Optional: try it on your phone over the same Wi-Fi**

1. Find your laptop's IP address: Windows `ipconfig` (look for *IPv4 Address*, e.g. `192.168.1.5`); Mac `ipconfig getifaddr en0`.
2. In `.env` add: `ALLOWED_HOSTS=127.0.0.1,localhost,192.168.1.5`
3. Run `python manage.py runserver 0.0.0.0:8000` and open `http://192.168.1.5:8000` on the phone.

**Optional: run the automatic checks** (24 tests of the billing, money and stock maths):
```bash
python manage.py test tests
```

---

## Step 2 — Put the code on GitHub (private)

1. Sign up at [github.com](https://github.com) → **New repository** → name `lavish-pos` → choose **Private** → **Create** (don't add a README).
2. In the terminal, inside the project folder:

```bash
git init
git add .
git commit -m "Lavish Shawarma POS"
git branch -M main
git remote add origin https://github.com/YOUR-USERNAME/lavish-pos.git
git push -u origin main
```
A browser window asks you to log in to GitHub the first time.

Check on GitHub that **`.env` and `db.sqlite3` are NOT in the list of files** (`.gitignore` keeps them out).

**Later, after any change:**
```bash
git add .
git commit -m "what I changed"
git push
```
Render redeploys automatically within a few minutes.

---

## Step 3 — Create the Supabase database

1. Sign up at [supabase.com](https://supabase.com) → **New project**.
   - Name: `lavish-pos`
   - **Database password**: click *Generate*, then **copy it somewhere safe**. Use letters and numbers only. Symbols like `@ # /` break the connection link.
   - **Region: Southeast Asia (Singapore)**. It's the same region as Render, so it's fast.
2. Wait about 2 minutes for it to finish.
3. Click **Connect** (top of the project page) → find **Session pooler** → copy the **URI**. It looks like:
   ```
   postgresql://postgres.abcdxyz:[YOUR-PASSWORD]@aws-0-ap-southeast-1.pooler.supabase.com:5432/postgres
   ```
   Replace `[YOUR-PASSWORD]` (including the brackets) with your database password.
   Use the **Session pooler**, not "Direct connection". Render can't reach the direct one.

**Test it from your laptop:** paste the URI into `.env`:
```
DATABASE_URL=postgresql://postgres.abcdxyz:YourPassword@aws-0-ap-southeast-1.pooler.supabase.com:5432/postgres
```
then run:
```bash
python manage.py migrate
python manage.py seed_shop
python manage.py ensure_owner
python manage.py runserver
```
Your laptop is now using the real cloud database. In Supabase → **Table Editor** you'll see tables like `orders_order`.

> To go back to the local test database, empty the `DATABASE_URL=` line again.

---

## Step 4 — Put it live on Render

1. Sign up at [render.com](https://render.com) **with your GitHub account**.
2. **New +** → **Blueprint** → pick the `lavish-pos` repository → **Connect**.
   Render reads `render.yaml` and asks for these values:
   - `DATABASE_URL`: the Supabase Session pooler URI from Step 3
   - `OWNER_PASSWORD`: a **strong** password for the owner login (the live site's login)
   - `ALLOWED_HOSTS`: leave **empty** for now
   - `CSRF_TRUSTED_ORIGINS`: leave **empty** for now
3. Click **Apply**. The first build takes 3–5 minutes. When it says **Live**, open the address shown (like `https://lavish-pos-xxxx.onrender.com`) and log in.

If the build fails, open **Logs**. The error is usually a typo in `DATABASE_URL`.

---

## Step 5 — Buy the domain on Cloudflare and connect it

1. Sign up at [cloudflare.com](https://dash.cloudflare.com) → **Domain Registration → Register Domains** → search e.g. `lavishshawarma.com` → buy it (turn on auto-renew).
2. In **Render** → your service → **Settings → Custom Domains** → **Add** `lavishshawarma.com`, then add `www.lavishshawarma.com` too. Render shows the DNS records it wants.
3. In **Cloudflare** → your domain → **DNS → Records → Add record**:

   | Type | Name | Target | Proxy status |
   |---|---|---|---|
   | CNAME | `@` | `lavish-pos-xxxx.onrender.com` | **DNS only** (grey cloud) |
   | CNAME | `www` | `lavish-pos-xxxx.onrender.com` | **DNS only** (grey cloud) |

   Keep the cloud **grey (DNS only)** so Render can issue the free HTTPS certificate.
4. Back in Render, click **Verify** next to each domain. Within 5–30 minutes both show a green **Certificate issued**.
5. In **Render → Environment**, set:
   ```
   ALLOWED_HOSTS=lavishshawarma.com,www.lavishshawarma.com
   CSRF_TRUSTED_ORIGINS=https://lavishshawarma.com,https://www.lavishshawarma.com
   ```
   Click **Save changes**. Render restarts the app. Open **https://lavishshawarma.com**.

---

## Step 6 — Keep it awake (important on the free plan)

Render's free plan **goes to sleep after 15 minutes with no visitors**, and the first visit after that takes about a minute. Supabase's free plan **pauses after 7 days with no activity**. One free pinger fixes both:

1. Sign up at [uptimerobot.com](https://uptimerobot.com) (free).
2. **Add New Monitor** → type **HTTP(s)** → URL `https://lavishshawarma.com/healthz` → interval **5 minutes** → Create.

`/healthz` also touches the database, so Supabase stays awake too. UptimeRobot will also email you if the site ever goes down.

---

## Step 7 — Turn on nightly backups

Supabase's free plan has no backups, so this repository backs itself up every night at 3:30 AM and keeps each backup for 30 days.

1. GitHub → your repo → **Settings → Secrets and variables → Actions → New repository secret**
   - Name: `SUPABASE_DB_URL`
   - Value: the same Session pooler URI from Step 3
2. **Actions** tab → **Nightly database backup** → **Run workflow** once, to test it. A green tick means it worked.
3. Backups are under each run → **Artifacts** (download → a `.dump` file inside a zip).

**To restore a backup** (only if something goes badly wrong). On a computer with PostgreSQL 17 tools installed:
```bash
pg_restore --clean --if-exists --no-owner --no-privileges -d "SESSION_POOLER_URI" lavish_2026-10-02_0330.dump
```
Ask for help before restoring. It replaces the current data.

---

## Step 8 — Install on each phone, tablet and laptop

- **Android (Chrome):** open the site → menu **⋮** → **Add to Home screen / Install app**.
- **iPhone (Safari):** **Share** → **Add to Home Screen**.
- **Laptop (Chrome/Edge):** the install icon in the address bar.

Each device logs in once and stays logged in for 30 days. Every bill records which device made it (rename devices under **Staff & devices**).

### Thermal receipt printer
- **Laptop + USB printer:** install the printer's driver and set paper to 58 mm or 80 mm. In the print window choose that printer, **Margins: None**, and untick **Headers and footers**. Pick 58/80 mm under **Settings → Receipt width**.
- **Android + Bluetooth printer:** install the free **RawBT** app (it adds the Bluetooth printer as a normal printer), pair the printer, then use **Print** in the app.
- In the payment window, tick **Print receipt automatically** to print after every payment on that device.
- **PDF** button on any bill makes a receipt PDF to share on WhatsApp.

---

## Daily routine at the shop

1. **Morning:** check **Prep plan** for how much chicken and how many rotis to prepare today.
2. **When you buy stock:** **Chicken & stock → Bought stock** (quantity + amount paid). This updates stock and records the expense.
3. **All day:** **Billing**. Tap items → **Pay** (or **Save · pay later** for dine-in) → give the token number.
4. **Burnt, spoiled or staff food:** record it under **Wastage** / **Staff meal**, so it isn't counted as missing.
5. **Other spending** (gas, vegetables, salary): **Expenses → Add**.
6. **Night:** **Day close**. Count the cash drawer and weigh or count the stock. The app shows:
   - **Cash short / extra** compared with what the bills say.
   - **Stock missing** per item, in kg/pieces and in ₹.

### First-week setup (owner)
1. **Change the starter amounts in Recipes** (Chicken & stock → Recipes). I set 120 g raw chicken per shawarma, 180 g for Lavish Special and the bowl, 1 roti per shawarma. **These are guesses.** To find yours: weigh the raw chicken going on the spit, count the shawarmas it made, then divide. Until you save this page, missing-stock numbers will be off.
2. **Menu & prices**: check every item and price. Add plates, rolls and extras such as cheese from here. You never need to touch code.
3. **Stock items**: set the low-stock warning levels.
4. **Settings**: shop phone number, receipt footer, staff limits.
5. **Staff & devices → Add staff** when you're ready to give staff their own logins.
6. On day 1, count all stock at **Day close**. From day 2 the missing-stock check is fully accurate.

---

## Who can do what

| | Owner | Staff |
|---|---|---|
| Billing, take payments, kitchen board | ✅ | ✅ |
| Edit an unpaid bill | ✅ | ✅ |
| Edit a paid bill, refund | ✅ | ❌ |
| Cancel a bill | ✅ | Asks owner (Approvals) |
| Discount | Any | Up to the % in Settings (default 0%), above that asks owner |
| Mark item sold out | ✅ | ✅ (can switch off in Settings) |
| Stock purchases, wastage, expenses, day close | ✅ | Only if turned on in Settings |
| Money dashboard, reports, exports, menu, prices, staff, audit log | ✅ | ❌ |

**Abuse limits for staff** (Settings): max unpaid orders open at once (default 15), max orders per minute (default 6). Wrong passwords lock that login for 15 minutes after 5 tries. Bills are never deleted, only cancelled with a reason. Every change is in the **Audit log**, and nobody can edit it.

---

## For the AI / ML phase

All data is stored in clean, ML-ready tables from day one (prices and names copied onto each bill, timestamps, business date, payment method, staff, device, stock usage, wastage, counts). After a few months:

```bash
python manage.py export_ml_data --out ml_data
```
creates `daily_item_sales.csv` (zero-filled date × item grid), `hourly_sales.csv`, `daily_stock.csv` and `daily_totals.csv`, ready for pandas / LightGBM.

The current prep plan (`stock/forecast.py`) averages the same weekday over the last 4 weeks. Swap in a trained model behind the same `forecast(day)` function and the Prep plan page uses it automatically.

---

## Project layout

```
config/      settings and URL list
core/        shop settings, devices, audit log, live sync, permissions, setup commands
accounts/    users (owner/staff), login protection, staff & devices pages
menu/        categories, items, sizes (variants), extras
orders/      POS screen, orders, payments, approvals, receipts (HTML + PDF)
                services.py ← all billing rules live here
stock/       stock items, recipes, purchases/wastage, daily counts, prep forecast
                services.py ← the "what's missing" maths
money/       expenses, day close, dashboard, reports, Excel/CSV export
templates/   all pages
static/      design (css/app.css), scripts, app icons
tests/       automatic checks
render.yaml  how Render runs the site      build.sh  what runs on each deploy
.github/workflows/backup.yml   nightly backup
```

## Troubleshooting

| Problem | Fix |
|---|---|
| `DisallowedHost` / “Bad Request (400)” | Add the address to `ALLOWED_HOSTS` (in `.env` locally, in Render → Environment live). |
| “CSRF verification failed” when logging in on your domain | Set `CSRF_TRUSTED_ORIGINS=https://yourdomain.com,https://www.yourdomain.com` in Render. |
| First load very slow | Render was asleep. Set up UptimeRobot (Step 6). |
| `password authentication failed` / `could not translate host` | Wrong `DATABASE_URL`: use the **Session pooler** URI and your real DB password with no brackets. |
| Forgot owner password | Render → **Environment** → set `OWNER_USERNAME` to a new name (e.g. `owner2`) and save. A fresh owner login is created on the next deploy. Then reset the old one under Staff & devices. |
| Want to look inside the data | Supabase → **Table Editor**, or the backup admin at `/admin/` (owner login). |
