# CLAUDE.md - Project Context

## Project Overview

**POS System** - A Point of Sale web application for managing products, categories, and generating receipts with Billingo invoicing API integration. Designed for Hungarian market (HUF currency, 27% VAT, Hungarian language support).

**Primary Use Case**: Market sales - prepare products with LOT numbers before going to the market, then sell using Market Mode which shows only prepared products + coffee shop items.

## Tech Stack

- **Backend**: Flask (Python 3)
- **Database**: SQLite 3 (`pos.db`)
- **Frontend**: Jinja2 templates, vanilla JavaScript, CSS
- **Authentication**: Google Sign-In
- **External APIs**: Billingo API v3 (invoicing), Cafetiko.com (product catalog)

## Project Structure

```
POS/
├── app.py                    # Main Flask application (all routes & logic)
├── requirements.txt          # Python dependencies (Flask, requests)
├── pos.db                    # SQLite database
├── api_desc.yaml             # Billingo OpenAPI 3.0 specification
├── templates/                # Jinja2 HTML templates
│   ├── main_menu.html        # Landing page with Google Sign-In
│   ├── prepare_market.html   # Market preparation - add products with LOT numbers
│   ├── market_mode.html      # Market sales interface
│   ├── create_receipt.html   # General receipt creation interface
│   ├── manage_items.html     # Item list/management
│   ├── manage_categories.html# Category list/management
│   ├── add_item.html         # Add item form
│   ├── edit_item.html        # Edit item form
│   ├── add_category.html     # Add category form
│   └── edit_category.html    # Edit category form
├── static/
│   └── style.css             # Global styles
└── env/                      # Python virtual environment
```

## Database Schema

```sql
-- Categories (with coffee shop flag for items that don't need LOT numbers)
CREATE TABLE categories (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    is_coffee_shop INTEGER DEFAULT 0
)

-- Items (products in catalog)
CREATE TABLE items (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    price REAL,
    vat TEXT DEFAULT '27%',
    category_id INTEGER,
    image_url TEXT,
    description TEXT,
    FOREIGN KEY (category_id) REFERENCES categories (id)
)

-- Market Sessions (active until manually closed)
CREATE TABLE market_sessions (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    closed_at TIMESTAMP
)

-- Market Session Items (products prepared for market with LOT numbers)
-- Same product can have multiple entries with different LOT numbers
CREATE TABLE market_session_items (
    id INTEGER PRIMARY KEY,
    session_id INTEGER NOT NULL,
    item_id INTEGER NOT NULL,
    lot_number TEXT NOT NULL,
    quantity_prepared INTEGER NOT NULL,
    quantity_remaining INTEGER NOT NULL,
    FOREIGN KEY (session_id) REFERENCES market_sessions(id),
    FOREIGN KEY (item_id) REFERENCES items(id)
)
```

## Key Features

### Market Mode Workflow

1. **Prepare Market** (`/prepare_market`)
   - Create a market session (e.g., "Saturday Market")
   - Select products from catalog
   - Enter LOT number and quantity for each
   - Same product can have multiple LOT numbers (different roast dates)
   - Session stays active until manually closed

2. **Market Mode** (`/market_mode`)
   - Shows only:
     - Prepared products with LOT numbers (stock tracked)
     - Coffee shop category items (no LOT needed)
   - Manual LOT selection when product has multiple LOTs
   - Stock quantity decreases on sale
   - Low stock warnings

3. **Receipt Generation**
   - Always electronic (e-nyugta)
   - Default email: `valaki@valaki.com` (for customers who don't need receipt)
   - X button to clear and enter customer email

### Coffee Shop Categories
Categories can be marked as "Coffee Shop" in category management. Items in these categories:
- Don't require LOT numbers
- Always visible in Market Mode
- For on-site prepared items (espresso, cappuccino, etc.)

## Key Routes

| Method | Route | Description |
|--------|-------|-------------|
| GET | `/` | Main menu |
| GET | `/prepare_market` | Market preparation page |
| POST | `/create_market_session` | Create new market session |
| POST | `/close_market_session` | Close active session |
| POST | `/add_market_item` | Add product with LOT to session |
| POST | `/remove_market_item/<id>` | Remove item from session |
| POST | `/update_market_item_quantity/<id>` | Update prepared quantity |
| GET | `/market_mode` | Market sales interface |
| POST | `/market_sale` | Update stock after sale |
| GET/POST | `/create_receipt` | Create receipt via Billingo |
| GET/POST | `/manage_categories` | Category CRUD |
| GET/POST | `/manage_items` | Item CRUD |
| POST | `/update_catalog` | Sync products from external API |

## External API Integrations

### Billingo API v3
- **Base URL**: `https://api.billingo.hu/v3`
- **Auth**: X-API-KEY header
- **Used for**: Receipt/invoice generation, PDF downloads
- **Partner ID**: 0, **Block ID**: 233585

### Cafetiko.com Product API
- **Base URL**: `https://cafetiko.com/api/`
- **Returns**: XML product/category data
- **Used for**: Catalog synchronization
- **Parses**: Hungarian language names (language id="2")

## Development

```bash
# Activate virtual environment
source env/bin/activate  # Linux/Mac
env\Scripts\activate     # Windows

# Install dependencies
pip install -r requirements.txt

# Run development server
python app.py
# Server runs at http://localhost:5000
```

## Environment Variables

- `FLASK_SECRET_KEY` - Required for session management

## Important Notes

1. **Monolithic Architecture**: All backend logic is in `app.py`
2. **No ORM**: Direct SQL queries with `sqlite3` module
3. **No Tests**: No testing infrastructure exists
4. **Debug Mode**: Currently enabled - disable for production
5. **Security**: API keys are hardcoded - should be moved to environment variables
6. **E-Nyugta**: All receipts are electronic, sent to email

## Common Tasks

### Setting Up for Market Day
1. Go to "Prepare Market" from main menu
2. Create new session with descriptive name
3. Select each product, enter LOT number (roast/pack date) and quantity
4. Repeat for all products being taken to market
5. Go to "Market Mode" when ready to sell

### Adding Coffee Shop Items
1. Go to "Manage Categories"
2. Edit or create a category
3. Check "Coffee Shop Category" checkbox
4. Add items to this category - they'll appear in Market Mode without needing LOT numbers

### Closing a Market Session
1. Go to "Prepare Market"
2. Click "Close Session"
3. This ends tracking for that session (stock data preserved for records)
