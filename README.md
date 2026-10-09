# Retail POS & Commercial Billing System

A modern, multi-tenant Point of Sale (POS) and retail management platform built with **Django**, **Django REST Framework**, and **Vanilla JavaScript**. Designed for commercial retail merchants, supermarkets, and multi-counter retail stores.

---

## Key Features

### 1. High-Speed Point of Sale (POS)
- **F2 Barcode & Product Search**: Dedicated modal overlay with real-time catalog search by product name, SKU, or EAN-13 barcode.
- **Hardware Scanner Compatible**: Native driverless USB and Bluetooth HID barcode scanner integration with automatic input buffering.
- **Mobile Camera Companion**: Built-in smartphone camera scanner at /pos/scanner/ accessible over local Wi-Fi with instant QR code pairing.
- **Full Keyboard Navigation**: POS shortcuts for rapid checkout (F2 Search, Enter Add, Esc Dismiss, F9 Settle).

### 2. Commercial Billing & Statutory Invoicing
- **Tax Calculation**: Automated split calculations for CGST, SGST, and IGST with configurable tax slabs.
- **Tender Options**: Cash, Card, UPI, and Customer Credit with automated credit balance tracking.
- **Thermal & Letterhead Printing**: Clean, high-contrast @media print receipts and detailed A4 GST invoices.

### 3. Multi-Tenant Architecture & Role-Based Access Control (RBAC)
- **Tenant Isolation**: Every registered merchant operates within an isolated tenant environment. Inventory, sales, customers, and payroll remain strictly private to each store.
- **Role Hierarchy**:
  - **Store Owner**: Complete administrative access, statutory settings, financial reporting, and employee management.
  - **Store Manager**: Inventory catalog, supplier purchase orders, stock adjustments, and sales oversight.
  - **Cashier**: Terminal checkout, barcode scanning, customer selection, and invoice printing.

### 4. Employee Management & Monthly Payroll
- **Staff Roster**: Role assignments, joining dates, departmental allocation, and salary configuration.
- **Historical Salary Ledger**: Month-by-month salary disbursement tracking covering all operating periods from April 2025 onwards.
- **Employee Payslips**: Individual payslip vouchers detailing basic salary, allowances, deductions, payment references, and one-click printable vouchers.

### 5. Automated Telegram Bot Reporting
- Real-time sales transaction receipts sent to your store Telegram group.
- Daily business digests: Gross revenue, transaction counts, and stock movement summaries.
- Low-stock and replenishment alerts for warehouse staff.

---

## Technology Stack

- **Backend**: Python 3.10+, Django 5+, Django REST Framework
- **Frontend**: Vanilla JavaScript (ES6+), Semantic HTML5, CSS Custom Properties (Theme Engine with Light/Dark modes)
- **Database**:
  - Development: Clean SQLite
  - Production / Cloud: PostgreSQL (Neon, Supabase, Render, Railway, AWS RDS)
- **Testing**: Django Test Framework, automated REST API tests, and Playwright / Chrome DevTools verification suites.

---

## Getting Started

### Prerequisites
- Python 3.10 or higher
- Git

### 1. Clone the Repository
`ash
git clone https://github.com/maheboob2/Billing-system.git
cd Billing-system
`

### 2. Set Up Virtual Environment
`ash
python -m venv .venv

# On Windows:
.venv\Scripts\activate

# On macOS / Linux:
source .venv/bin/activate
`

### 3. Install Dependencies
`ash
pip install -r requirements.txt
`

### 4. Configure Environment Variables
`ash
cp .env.example .env
`
Open .env and configure your settings. For local evaluation, default values work out of the box.

### 5. Run Database Migrations
`ash
cd Billing_System
python manage.py migrate
`

### 6. Create Administrator / Owner Account
`ash
python manage.py createsuperuser
`
*(Or use the self-service store registration form at /registration/ to provision a merchant tenant and store owner profile).*

### 7. Start the Development Server
`ash
python manage.py runserver
`
Visit http://127.0.0.1:8000 in your web browser.

---

## Production & Cloud Deployment (PostgreSQL)

To deploy on hosted platforms (Render, Railway, Fly.io, Heroku, or AWS):

1. Set DEBUG=False in your cloud environment variables.
2. Provide your hosted database connection string:
   `env
   DATABASE_URL=postgresql://db_user:db_password@db_host:5432/pos_billing_db
   `
3. Set your production SECRET_KEY and ALLOWED_HOSTS.
4. Run migrations:
   `ash
   python manage.py migrate
   `

---

## Project Structure

`
Billing-system/
├── .env.example               # Environment variables template
├── .gitignore                  # Git ignore rules (DBs and secrets excluded)
├── requirements.txt            # Python dependencies
├── README.md                   # Project documentation
└── Billing_System/             # Django project root
    ├── manage.py
    ├── config/                 # Core settings, WSGI/ASGI, and URL routing
    ├── accounts/               # Multi-tenant authentication and registration
    ├── dashboard/              # Store metrics, Telegram daemon, and settings
    ├── sales/                  # POS cart terminal, barcode lookup, and invoices
    ├── inventory/              # Product catalog, stock levels, and categories
    ├── purchases/              # Supplier purchase orders and incoming stock
    ├── employee/               # Staff management, payroll ledger, and payslips
    ├── reports/                # Analytics, revenue charts, and audit reports
    └── Home/                   # Public landing page and routing
`

---

## License & Attribution

This project is open-source and available under the standard repository license. Maintained by the Billing-system project team.
