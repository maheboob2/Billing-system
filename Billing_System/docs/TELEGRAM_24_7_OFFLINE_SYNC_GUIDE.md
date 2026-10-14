# 24/7 Telegram Bot & Offline Cloud Synchronization Guide

This guide details the complete architecture, deployment process, and operating commands for running the Retail POS Telegram Bot independently 24/7 with zero dependence on the shop PC being turned on.

---

## 1. High-Level Architecture

```
┌─────────────────────────────────────────┐          ┌─────────────────────────────────────────┐
│     Local Shop POS (Windows PC)         │          │     24/7 Cloud Service (Render/VPS)     │
│                                         │          │                                         │
│ • Cashier Billing & Offline Sales       │  Sync    │ • Persistent Telegram Worker Daemon     │
│ • Local SQLite DB (`db.sqlite3`)        │─────────>│ • Cloud Sync Ingestion Endpoint         │
│ • Sync Buffer (`SyncQueueItem`)         │  (Push)  │ • Durable Snapshot Store                │
│ • Auto-Sync Engine (`AutoSyncDaemon`)   │          │   (`CompanyCloudSnapshot`)              │
└─────────────────────────────────────────┘          └────────────────────┬────────────────────┘
                                                                          │
                                                                   Answers 24/7
                                                                   (Live / Cached)
                                                                          │
                                                                          ▼
                                                             ┌─────────────────────────┐
                                                             │   Store Owner Phone     │
                                                             │   (Telegram App)        │
                                                             └─────────────────────────┘
```

### Key Guarantees:
1. **Always-On Telegram Bot (24/7 Availability)**:
   - The bot worker runs in a hosted cloud environment (Render, Railway, Fly.io, or VPS).
   - Availability does **NOT** depend on:
     - The shop PC being turned on.
     - Django `runserver` running locally.
     - A terminal window remaining open.
     - The local POS database being continuously reachable.
2. **Transparent Freshness States**:
   - `🟢 LIVE DATA`: Store is online and in-store sales are synced in real-time.
   - `🟡 CACHED`: Store PC is closed/offline; data is served from the latest cloud snapshot (< 30 minutes old).
   - `🟠 STALE`: Store PC has been offline for over 30 minutes; snapshot timestamp is prominently displayed.
   - `⚠️ SYNC PENDING`: In-store transactions were completed offline and are awaiting internet reconnection.
3. **Data Safety & Idempotency**:
   - Reconnection or retrying sync **never** duplicates sales, payments, or stock movements (`idempotency_key` deduplication).
   - Sensitive credentials, customer passwords, and raw employee payroll details are **never** synced to cloud snapshots.
4. **Tenant Isolation & Role Permissions**:
   - Each store's snapshots and Telegram links are strictly isolated by `company_id`.
   - Owners and Managers receive executive financial reports; Cashiers receive only cashier-level collections; unauthorized users are rejected.

---

## 2. 24/7 Deployment Options

### Option A: Render (Recommended - Free / Starter Tier)
1. Push your repository to GitHub.
2. Log in to [Render.com](https://render.com) and click **New + > Blueprint**.
3. Connect your GitHub repository. Render automatically reads `Billing_System/render.yaml`.
4. Render creates two services:
   - **`billing-cloud-api`** (Web Service): Provides the cloud sync ingestion endpoint `/api/cloud-sync/push/`.
   - **`billing-telegram-worker`** (Background Worker): Runs `python manage.py run_telegram_bot` 24/7.
5. In Render's Dashboard, set the environment variable:
   - `TELEGRAM_BOT_TOKEN`: Your Telegram Bot Token obtained from `@BotFather`.
   - `CLOUD_SYNC_API_KEY`: A secure random string (e.g., `sk_live_sync_xyz123`).

### Option B: Docker / VPS
1. Build the Docker container:
   ```bash
   docker build -t billing-pos-bot:latest .
   ```
2. Run the worker daemon continuously:
   ```bash
   docker run -d --restart=always --name billing-telegram-worker \
     -e TELEGRAM_BOT_TOKEN="your_token_here" \
     -e DEBUG="False" \
     billing-pos-bot:latest
   ```

---

## 3. Configuring Local POS for Cloud Synchronization

In your local shop environment (`Billing_System/.env`), add:
```ini
# Remote 24/7 Cloud Service URL
CLOUD_SYNC_REMOTE_URL=https://billing-cloud-api.onrender.com

# Shared API Key for authenticated sync push
CLOUD_SYNC_API_KEY=sk_live_sync_xyz123
```

When configured:
1. `SyncService.run_sync()` automatically pushes the latest financial snapshot to the cloud service every 30 seconds.
2. If the shop loses internet connectivity, transactions are buffered locally in `SyncQueueItem`.
3. When internet is restored, all offline sales synchronize automatically with zero duplicate records.

---

## 4. Telegram Natural Language Reporting Commands

| Command / Query | Description |
| :--- | :--- |
| `/start` | Check bot connectivity and view pairing status |
| `/report` or `today's report` | Complete financial report for today |
| `yesterday's report` | Yesterday's revenue, tender breakdown, and refunds |
| `sales on 2026-10-08` | Specific date sales report |
| `sales from 2026-10-01 to 2026-10-08` | Date range revenue report |
| `how much cash did we collect today?` | Cash tender collections and percentage |
| `show upi collections` | UPI / QR code payment totals |
| `card payments` | Credit / Debit card transaction totals |
| `inventory stock overview` | Low stock alerts (≤ 5 units) and out of stock SKUs |
| `top selling products` | Top 5 products by revenue and volume |
| `customer credit balances` | Outstanding customer khata / credit accounts |
| `send me a pdf report` | Generates and sends an executive PDF report |
| `show chart image` | Generates a high-resolution financial visual chart |
