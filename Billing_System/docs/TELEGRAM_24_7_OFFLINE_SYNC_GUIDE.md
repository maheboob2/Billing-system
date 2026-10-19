# 24/7 Telegram Bot & Offline-First Cloud Synchronization Guide

This guide details the complete offline-first synchronization architecture, operational lifecycle, deployment process, and operating commands for running the Retail POS Telegram Bot independently 24/7 with zero dependence on the shop PC being turned on.

---

## 1. High-Level Architecture

```
┌──────────────────────────────────────────────┐              ┌──────────────────────────────────────────────┐
│       Local Shop POS (Windows Edge PC)       │              │       24/7 Cloud Service (Render/VPS)        │
│                                              │              │                                              │
│ • Local POS Billing, Inventory & Returns     │  Encrypted   │ • Ingestion API (`/api/cloud-sync/push/`)    │
│ • Local SQLite DB (`db.sqlite3`)             │  HTTPS Push  │ • Idempotent Ingestion (`RemoteSyncedRecord`)│
│ • Transactional Outbox (`SyncQueueItem`)     │─────────────>│ • Materialized Snapshot                      │
│ • Background Worker (`run_sync_worker`)      │  (ACK Check) │   (`CompanyCloudSnapshot`)                   │
│ • Exponential Backoff & Local Checkpoints    │              │ • Independent Bot (`run_telegram_bot`)       │
└──────────────────────────────────────────────┘              └──────────────────────┬───────────────────────┘
                                                                                     │
                                                                             Answers Owner 24/7
                                                                             (Fresh or Stale Notice)
                                                                                     │
                                                                                     ▼
                                                                        ┌─────────────────────────┐
                                                                        │   Store Owner Phone     │
                                                                        │   (Telegram App)        │
                                                                        └─────────────────────────┘
```

---

## 2. Core Operational Scenarios

### Scenario A — Shop and Internet are Online
1. In-store cashier conducts billing, returns, and inventory adjustments normally.
2. Changes are written atomically to the local database and enqueued into `SyncQueueItem` with stable `event_id` and unique `idempotency_key`.
3. Background sync worker (`run_sync_worker` or in-process `AutoSyncDaemon`) pushes the pending outbox batch and materialized business snapshot to the authenticated remote cloud API (`/api/cloud-sync/push/`) via HTTPS with `X-Cloud-Sync-Key`.
4. The cloud API validates authorization and tenant identity, records items idempotently, saves the snapshot, and returns HTTP 200 with `acknowledged_keys`.
5. The local worker marks only acknowledged items as `SYNCED` and updates the local checkpoint.
6. The store owner queries Telegram and receives real-time live data:
   ```
   📊 Today's Sales Report (Oct 17, 2026)
   Store: Alpha Mart
   ────────────────────────
   • Gross Sales: ₹12,450.00
   • Net Revenue: ₹12,450.00
   • Cash: ₹5,000.00 | UPI/QR: ₹7,450.00
   ────────────────────────
   🕒 Report Generated: Oct 17, 2026 04:30 PM
   🔄 Data Through (Last Sync): Oct 17, 2026 04:30 PM
   🟢 Live Data (Store online, synchronized)
   ```

### Scenario B — Shop Internet is Disconnected (Local POS Running)
1. In-store POS cashier operations continue uninterrupted on the local LAN.
2. All sales, stock mutations, payments, and returns are durably persisted in local SQLite.
3. Every mutation creates a durable outbox entry in `SyncQueueItem` with `status="PENDING"`.
4. The local sync worker catches the connection error and backs off exponentially (15s → 30s → 60s → 120s max).
5. The owner queries Telegram. The independently hosted cloud bot reads the most recent valid snapshot from `CompanyCloudSnapshot` and clearly displays stale warning:
   ```
   📊 Today's Sales Report — Last Synced Snapshot
   Store: Alpha Mart
   ────────────────────────
   • Gross Sales: ₹12,450.00
   ────────────────────────
   🕒 Report Generated: Oct 17, 2026 05:15 PM
   🔄 Data Through (Last Sync): Oct 17, 2026 04:30 PM
   ⚠️ Report may be stale — Store Offline Snapshot
   ⚠️ Store Offline: 8 local pending items. Newer offline sales or stock activity may be missing from this report.
   📡 Freshness: CACHED (Last Cloud Sync: Oct 17, 2026 04:30 PM)
   Your store is currently disconnected or has not confirmed a newer sync. Recent local transactions may not be included.
   ```

### Scenario C — The Shop PC/Server is Shut Down
1. The shop PC is completely powered off overnight or during power outages.
2. The Telegram bot is independently hosted in the cloud and does NOT depend on a live connection to the shop PC.
3. Store owners can query sales, inventory, and tender reports 24 hours a day, 7 days a week.
4. Reports show the exact timestamp when the shop was last synced and an offline notice.

### Scenario D — The Shop Reconnects
1. The local sync worker detects internet availability on the next check.
2. It resumes sending unacknowledged records from the last confirmed checkpoint.
3. Each record contains a deterministic `idempotency_key` (e.g. `SALE-<company_id>-<sale_number>`).
4. If network drops mid-request and retries, the cloud endpoint checks existing `RemoteSyncedRecord` and skips duplicate insertion.
5. Once acknowledged, the local outbox marks items `SYNCED`.
6. Subsequent Telegram queries immediately reflect the freshly confirmed data.

---

## 3. Data Integrity & Conflict Handling

- **Durable Local Outbox**: No transaction is considered synced until remote HTTP 200 with `acknowledged_keys` is received.
- **Strict Idempotency**: Retries, process restarts, and network duplicates produce zero duplicate records in `RemoteSyncedRecord`.
- **Tenant Isolation**: Both the Ingestion API and Telegram Bot strictly enforce `company_id` isolation. Tenant A cannot inject or view Tenant B data.
- **Monetary Precision**: All arithmetic is performed in Python `Decimal` and formatted to 2 decimal places. Floating-point drift is strictly avoided.
- **Stable Event IDs**: Each mutation receives a stable event ID:
  - Sale: `evt_sale_<company_id>_<sale_id>_<sale_number>`
  - Payment: `evt_pay_<payment_id>`
  - Return: `evt_ret_<company_id>_<return_id>`
  - Stock Movement: `evt_stock_<company_id>_<movement_id>`

---

## 4. Environment Configuration

### Local Shop Edge PC (`Billing_System/.env`)
```ini
DEBUG=True
ALLOWED_HOSTS=localhost,127.0.0.1,testserver

# Edge instance role
CLOUD_SYNC_ROLE=LOCAL_EDGE

# Remote cloud ingestion URL
CLOUD_SYNC_REMOTE_URL=https://billing-cloud-api.onrender.com/api/cloud-sync/push/

# Shared secret API Key
CLOUD_SYNC_API_KEY=pos-cloud-sync-secret-prod-key

# In-process background daemon
ENABLE_AUTO_SYNC=True
```

### Remotely Hosted Cloud Server (`Render / VPS / AWS`)
```ini
DEBUG=False
ALLOWED_HOSTS=billing-cloud-api.onrender.com,yourdomain.com

# Cloud instance role
CLOUD_SYNC_ROLE=CLOUD_WORKER

# Shared secret API Key matching the edge POS key
CLOUD_SYNC_API_KEY=pos-cloud-sync-secret-prod-key

# 24/7 Telegram Bot Token from @BotFather
TELEGRAM_BOT_TOKEN=123456789:ABCdefGHIjklMNOpqrsTUVwxyz1234567

# Persistent Database (PostgreSQL recommended in cloud)
DATABASE_URL=postgresql://user:password@cloud-db-host:5432/pos_db
```

---

## 5. Management Commands

### 1. Run Background Sync Worker
```bash
# Continuous background daemon (checks outbox every 30 seconds)
python manage.py run_sync_worker --interval 30

# Single execution pass (useful for cron jobs or scripted health checks)
python manage.py run_sync_worker --once

# Filter sync to a specific company ID or name
python manage.py run_sync_worker --company 1 --once
```

### 2. Run Standalone Telegram Bot Daemon (Cloud Worker)
```bash
# Continuous long-polling worker
python manage.py run_telegram_bot

# Single poll pass (useful for diagnostics or testing)
python manage.py run_telegram_bot --once
```

---

## 6. Provider-Neutral Cloud Deployment Options

### Option A: Render (Free / Starter Tier)
The repository includes `Billing_System/render.yaml` which defines:
1. `billing-cloud-api` (Web Service): Runs `gunicorn config.wsgi:application` and hosts `/api/cloud-sync/push/`.
2. `billing-telegram-worker` (Background Worker): Runs `python manage.py run_telegram_bot` 24/7.

### Option B: Linux VPS / systemd Service
Create `/etc/systemd/system/telegram-bot.service`:
```ini
[Unit]
Description=Billing System 24/7 Telegram Worker
After=network.target

[Service]
User=posuser
WorkingDirectory=/opt/Billing-system/Billing_System
ExecStart=/opt/Billing-system/.venv/bin/python manage.py run_telegram_bot
Restart=always
RestartSec=5
EnvironmentFile=/opt/Billing-system/Billing_System/.env

[Install]
WantedBy=multi-user.target
```
Enable and start the service:
```bash
sudo systemctl daemon-reload
sudo systemctl enable --now telegram-bot.service
```

### Option C: Docker Container
```bash
# Build image
docker build -t billing-system:latest -f Dockerfile .

# Run Telegram Worker
docker run -d --restart=always \
  --name telegram-bot-worker \
  --env-file .env \
  billing-system:latest python manage.py run_telegram_bot
```

---

## 7. Supported Telegram Owner Queries

| Owner Question / Query | Handled By | Output |
| :--- | :--- | :--- |
| `How much did my shop sell today?` | `SALES_TODAY` | Gross sales, net revenue, order count, AOV, tender breakdown |
| `What is the current stock in the last synced snapshot?` | `INVENTORY` | Total catalog SKUs, total stock volume, active items |
| `Which products have low stock?` | `INVENTORY` | Detailed list of items with stock ≤ minimum safety threshold |
| `What returns are pending?` | `RETURNS` | Total returns, refunded amount, and pending approval count |
| `What are the cash, UPI, and credit totals?` | `PAYMENTS` | Complete tender breakdown with amounts and percentages |
| `today's report` | `SALES_TODAY` | Full closing summary report |
| `yesterday's report` | `SALES_YESTERDAY` | Yesterday's performance summary |
| `send me a pdf report` | `PDF_REPORT` | High-resolution executive closing PDF attachment |
| `show chart image` | `CHART_SALES` | 1600x1000px high-resolution revenue and performance visual chart |
