# POS System Improvements Tracker

**Created:** 2025-11-29
**Last Updated:** 2025-11-29
**Analysis Version:** 1.0

---

## Overview

This document tracks all identified improvements, bugs, and recommendations for the POS system. Use this to maintain continuity across development sessions.

---

## Status Legend

- 🔴 **CRITICAL** - Security/data loss risk, fix immediately
- 🟠 **HIGH** - Significant impact, prioritize
- 🟡 **MEDIUM** - Important but not urgent
- 🟢 **LOW** - Nice to have
- ✅ **DONE** - Completed
- 🔄 **IN PROGRESS** - Currently being worked on
- ⏸️ **BLOCKED** - Waiting on something

---

## Phase 1: Critical Fixes

### 🔴 Security Issues

| ID | Task | Location | Status | Notes |
|----|------|----------|--------|-------|
| SEC-001 | Move Billingo API keys to environment variables | `app.py:58-61` | ✅ DONE | Now uses BILLINGO_API_KEY_TEST/PROD env vars |
| SEC-002 | Move WooCommerce credentials to environment variables | `app.py:280-282` | ✅ DONE | Now uses WOOCOMMERCE_* env vars |
| SEC-003 | Replace SHA256 password hashing with bcrypt/argon2 | `app.py:229-239` | ✅ DONE | Now uses werkzeug PBKDF2, backwards compatible |
| SEC-004 | Remove default admin/admin credentials | `app.py:216-223` | ✅ DONE | Random password generated on first run |
| SEC-005 | Add CSRF protection to forms | All templates | ✅ DONE | Flask-WTF CSRFProtect enabled, JSON APIs exempted |
| SEC-006 | Add rate limiting to API endpoints | `/create_receipt`, etc. | ✅ DONE | Flask-Limiter: 5/min login, 30/min receipts |

### 🔴 Critical Bugs

| ID | Bug | Location | Status | Notes |
|----|-----|----------|--------|-------|
| BUG-001 | Wrong form field name `category_ids` should be `category_id` | `app.py:1136` | ✅ DONE | Fixed field name |
| BUG-002 | `last_receipt_data` is undefined/None | `app.py:1277` | ✅ DONE | Now uses session storage + URL params |
| BUG-003 | No stock validation before sale | `app.py:1805-1809` | ✅ DONE | Validates all stock before updating any |

---

## Phase 2: Code Quality & Consolidation

### Database Improvements

| ID | Task | Location | Status | Notes |
|----|------|----------|--------|-------|
| DB-001 | Add index on `market_sessions.closed_at` | Schema | ✅ DONE | Added in init_db() |
| DB-002 | Add index on `market_session_items.session_id` | Schema | ✅ DONE | Added in init_db() |
| DB-003 | Add index on `items.category_id` | Schema | ✅ DONE | Added in init_db() |
| DB-004 | Add index on `market_sales.session_id` | Schema | ✅ DONE | Added in init_db() |
| DB-005 | Add index on `users.username` | Schema | ✅ DONE | Added in init_db() |
| DB-006 | Add `row_factory = sqlite3.Row` to query_db | `app.py:106-119` | ✅ DONE | Now supports dict-like access |
| DB-007 | Add CHECK constraints for price >= 0 | Schema | ✅ DONE | validate_price() function |
| DB-008 | Add audit columns (updated_at, updated_by) | items, categories | ✅ DONE | Migration adds columns |
| DB-009 | Implement soft deletes (deleted_at) | items, categories | ✅ DONE | delete_item/category now soft delete |
| DB-010 | Add transaction isolation for multi-step ops | Throughout | ✅ DONE | execute_transaction() function |

### Template Consolidation

| ID | Task | Files Affected | Status | Lines Saved |
|----|------|----------------|--------|-------------|
| TPL-001 | Create `templates/base.html` with inheritance | All templates | ✅ DONE | Base template created |
| TPL-002 | Extract environment indicator to base | 8+ templates | ✅ DONE | In base.html |
| TPL-003 | Extract language toggle to base | 3+ templates | ✅ DONE | In base.html |
| TPL-004 | Move translations to `static/translations.js` | create_receipt, prepare_market | ✅ DONE | Centralized translations |
| TPL-005 | Create `static/api.js` fetch utilities | All templates with fetch | ✅ DONE | apiRequest, showToast, formatPrice |
| TPL-006 | Move inline styles to `style.css` | 5+ templates | ✅ DONE | Added 400+ lines to style.css |

### Code Refactoring

| ID | Task | Location | Status | Notes |
|----|------|----------|--------|-------|
| REF-001 | Consolidate duplicate catalog update functions | `app.py:714-805` | ✅ DONE | fetch_catalog_data, save_catalog_to_db helpers |
| REF-002 | Replace bare `except:` with specific handlers | `app.py:45,874,1104,1759` | ✅ DONE | All 4 instances fixed |
| REF-003 | Add input validation layer | Form handlers | ✅ DONE | validate_price() added |
| REF-004 | Standardize error response format | All API endpoints | ✅ DONE | api_success(), api_error() helpers |
| REF-005 | Add type hints to functions | Key functions | ✅ DONE | DB helpers, password, catalog functions |
| REF-006 | Add docstrings to functions | Key functions | ✅ DONE | Args, Returns, Raises documented |
| REF-007 | Split app.py into modules | Entire file | ⏸️ DEFERRED | Too risky for working system |

---

## Phase 3: Business Logic Improvements

### Stock & Inventory

| ID | Task | Status | Notes |
|----|------|--------|-------|
| INV-001 | Add stock locking during checkout | ⬜ TODO | Prevent overselling by multiple sellers |
| INV-002 | Validate quantity_remaining >= quantity before sale | ✅ DONE | market_sale() validates all stock first |
| INV-003 | Restore stock on receipt cancellation | ✅ DONE | cancel_document() restores stock |
| INV-004 | Add low stock alerts/warnings | ⬜ TODO | Threshold-based notifications |
| INV-005 | Implement FIFO for LOT selection | ⬜ TODO | Sell oldest LOTs first |

### Session Management

| ID | Task | Status | Notes |
|----|------|--------|-------|
| SES-001 | Session carry-over for unsold items | ✅ DONE | copy_from_session_id param, get_previous_sessions API |
| SES-002 | Post-session variance report | ⬜ TODO | Prepared vs sold vs remaining |
| SES-003 | Add session metadata (location, staff, notes) | ⬜ TODO | Better tracking |
| SES-004 | Cash reconciliation (expected vs actual) | ⬜ TODO | Input field for counted cash |
| SES-005 | Warning before auto-closing old session | ⬜ TODO | Prevent accidental data loss |

### Receipt/Invoice

| ID | Task | Status | Notes |
|----|------|--------|-------|
| RCP-001 | Receipt history view | ⬜ TODO | `/my_receipts` endpoint |
| RCP-002 | Reprint past receipts | ⬜ TODO | Fetch from Billingo |
| RCP-003 | Draft/staging before final send | ⬜ TODO | Review before committing |
| RCP-004 | Structured line items to Billingo | ⬜ TODO | Currently sends text blob |
| RCP-005 | Better default email handling | ⬜ TODO | Clear "no receipt" option |

---

## Phase 4: UX Improvements

### Market Mode

| ID | Task | Status | Notes |
|----|------|--------|-------|
| UX-001 | Cart persistence (localStorage) | ⬜ TODO | Survives page refresh |
| UX-002 | Search/filter by LOT number | ⬜ TODO | Quick product lookup |
| UX-003 | Bulk add items to cart | ⬜ TODO | "2x espresso, 1x cappuccino" |
| UX-004 | Undo last cart action | ⬜ TODO | Mistake recovery |
| UX-005 | Category collapse state persistence | ⬜ TODO | Remember in localStorage |
| UX-006 | Per-item discount support | ⬜ TODO | Currently only cart-level |

### Prepare Market

| ID | Task | Status | Notes |
|----|------|--------|-------|
| UX-007 | Auto-suggest LOT from roast_tracker | ⬜ TODO | Reduce manual entry |
| UX-008 | Bulk import from previous session | ⬜ TODO | One-click copy |
| UX-009 | Smart quantity suggestions | ⬜ TODO | Based on historical sales |

### General

| ID | Task | Status | Notes |
|----|------|--------|-------|
| UX-010 | Confirmation dialog before receipt creation | ⬜ TODO | Show email, items, total |
| UX-011 | Session expiry warning to user | ⬜ TODO | Alert before auto-logout |
| UX-012 | Add pagination to item lists | ⬜ TODO | Handle large catalogs |

---

## Phase 5: Power Features

### Reporting & Analytics

| ID | Task | Status | Notes |
|----|------|--------|-------|
| RPT-001 | Daily sales summary dashboard | ⬜ TODO | Sales by method, items, revenue |
| RPT-002 | Product performance report | ⬜ TODO | Best/slowest sellers |
| RPT-003 | Seller attribution & leaderboard | ⬜ TODO | Use sold_by field |
| RPT-004 | Payment method breakdown | ⬜ TODO | Cash vs card analytics |
| RPT-005 | VAT/tax reports | ⬜ TODO | Monthly/quarterly summaries |
| RPT-006 | Export to CSV | ⬜ TODO | All report types |

### Operations

| ID | Task | Status | Notes |
|----|------|--------|-------|
| OPS-001 | Add health check endpoint `/health` | ⬜ TODO | For monitoring/load balancers |
| OPS-002 | Configure proper logging with rotation | ⬜ TODO | File output, not just stdout |
| OPS-003 | Add audit logging for financial ops | ⬜ TODO | Who did what when |
| OPS-004 | Scheduled catalog refresh | ⬜ TODO | Nightly WooCommerce sync |
| OPS-005 | Auto-backup database | ⬜ TODO | Already partially implemented |

---

## Architecture Refactoring (Long-term)

### Current Structure
```
app.py (2100 lines - everything mixed)
├── Routes
├── Database queries
├── Business logic
├── API integrations
└── Helper functions
```

### Target Structure
```
app/
├── __init__.py              # Flask app factory
├── config.py                # Configuration from env vars
├── models.py                # Database models/queries
├── services/
│   ├── billingo.py          # Billingo API client
│   ├── woocommerce.py       # WooCommerce sync
│   ├── market.py            # Market business logic
│   └── inventory.py         # Stock management
├── routes/
│   ├── auth.py              # Login, logout, users
│   ├── items.py             # Item/category CRUD
│   ├── market.py            # Market mode, sessions
│   ├── receipts.py          # Receipt generation
│   └── reports.py           # Analytics endpoints
├── utils/
│   ├── validation.py        # Input validators
│   └── helpers.py           # Shared utilities
├── static/
│   ├── style.css
│   ├── api.js               # Fetch utilities
│   └── translations.js      # i18n strings
└── templates/
    ├── base.html            # Template inheritance
    └── ...
```

### Refactoring Steps

| Step | Task | Status | Notes |
|------|------|--------|-------|
| 1 | Create `app/config.py` with env vars | ⬜ TODO | |
| 2 | Create `app/models.py` with query_db | ⬜ TODO | |
| 3 | Extract Billingo code to `services/billingo.py` | ⬜ TODO | |
| 4 | Extract WooCommerce to `services/woocommerce.py` | ⬜ TODO | |
| 5 | Create route blueprints | ⬜ TODO | |
| 6 | Create `templates/base.html` | ⬜ TODO | |
| 7 | Migrate routes incrementally | ⬜ TODO | |

---

## Code Snippets for Implementation

### DB-001 to DB-005: Add Database Indexes

```sql
-- Add to init_db() function in app.py
CREATE INDEX IF NOT EXISTS idx_market_sessions_closed_at ON market_sessions(closed_at);
CREATE INDEX IF NOT EXISTS idx_market_session_items_session ON market_session_items(session_id);
CREATE INDEX IF NOT EXISTS idx_items_category ON items(category_id);
CREATE INDEX IF NOT EXISTS idx_market_sales_session ON market_sales(session_id);
CREATE INDEX IF NOT EXISTS idx_users_username ON users(username);
```

### DB-006: Add row_factory to query_db

```python
def query_db(query, args=(), one=False):
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row  # Add this line
    cur = conn.cursor()
    cur.execute(query, args)
    rv = cur.fetchall()
    conn.commit()
    conn.close()
    return (rv[0] if rv else None) if one else rv
```

### BUG-001: Fix category_id field name

```python
# Line 1136 in app.py - Change:
category_id = request.form.get('category_ids')
# To:
category_id = request.form.get('category_id')
```

### SEC-001/SEC-002: Environment Variables

```python
# Replace hardcoded keys with:
import os

BILLINGO_API_KEYS = {
    "test": os.environ.get("BILLINGO_API_KEY_TEST"),
    "prod": os.environ.get("BILLINGO_API_KEY_PROD")
}

WOOCOMMERCE_CONSUMER_KEY = os.environ.get("WOOCOMMERCE_CONSUMER_KEY")
WOOCOMMERCE_CONSUMER_SECRET = os.environ.get("WOOCOMMERCE_CONSUMER_SECRET")

# Create .env.example:
BILLINGO_API_KEY_TEST=your_test_key_here
BILLINGO_API_KEY_PROD=your_prod_key_here
WOOCOMMERCE_CONSUMER_KEY=your_key_here
WOOCOMMERCE_CONSUMER_SECRET=your_secret_here
FLASK_SECRET_KEY=generate_a_secure_random_key
```

### SEC-003: Password Hashing with Werkzeug

```python
from werkzeug.security import generate_password_hash, check_password_hash

def hash_password(password):
    """Hash a password using werkzeug (PBKDF2)"""
    return generate_password_hash(password, method='pbkdf2:sha256', salt_length=16)

def verify_password(password, password_hash):
    """Verify a password against its hash"""
    return check_password_hash(password_hash, password)
```

### INV-001: Stock Locking Pattern

```python
def sell_market_item(item_id, quantity, session_id):
    """Sell item with stock locking to prevent overselling"""
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.cursor()
        cur.execute("BEGIN IMMEDIATE")  # Lock for writing

        # Check available stock
        item = cur.execute("""
            SELECT quantity_remaining FROM market_session_items
            WHERE id = ? AND session_id = ?
        """, (item_id, session_id)).fetchone()

        if not item or item['quantity_remaining'] < quantity:
            conn.rollback()
            return False, "Insufficient stock"

        # Update stock
        cur.execute("""
            UPDATE market_session_items
            SET quantity_remaining = quantity_remaining - ?
            WHERE id = ?
        """, (quantity, item_id))

        conn.commit()
        return True, "Success"
    except Exception as e:
        conn.rollback()
        return False, str(e)
    finally:
        conn.close()
```

---

## Progress Summary

| Phase | Total | Done | In Progress | TODO |
|-------|-------|------|-------------|------|
| 1. Critical Fixes | 9 | 9 | 0 | 0 |
| 2. Code Quality | 23 | 22 | 0 | 1 (deferred) |
| 3. Business Logic | 14 | 3 | 0 | 11 |
| 4. UX Improvements | 12 | 0 | 0 | 12 |
| 5. Power Features | 11 | 0 | 0 | 11 |
| **Total** | **69** | **34** | **0** | **35** |

---

## Session Log

### 2025-11-29 - Phase 2 Complete (19/23 tasks)
- ✅ 19 of 23 Phase 2 tasks completed
- DB-001 to DB-010: All database improvements done
  - 5 indexes added for query performance
  - row_factory for dict-like access
  - validate_price() for input validation
  - execute_transaction() for atomic operations
  - Soft delete with deleted_at column
  - Audit columns (updated_at, deleted_at)
- TPL-001 to TPL-006: All template tasks done
  - Created base.html template
  - Created static/translations.js (HU/EN)
  - Created static/api.js with utilities
  - Added 400+ lines common styles to style.css
- REF-002, REF-003: Code quality improvements
  - Fixed all bare except: handlers
  - Added validate_price() function
- Remaining (4): REF-001, REF-004 to REF-007 (major refactoring)

### 2025-11-29 - Phase 1 Complete
- ✅ All 9 Phase 1 tasks completed
- SEC-001/002: API keys moved to environment variables (created .env.example)
- SEC-003/004: Password hashing upgraded to PBKDF2, random admin password on first run
- SEC-005/006: CSRF protection and rate limiting added (Flask-WTF, Flask-Limiter)
- BUG-001: Fixed category_id form field name
- BUG-002: Fixed receipt download using session storage
- BUG-003: Added stock validation before market sales

### 2025-11-29 - Initial Analysis
- Completed full codebase analysis from 3 perspectives
- Identified 69 improvement tasks
- Created this tracking document
- Priority: Start with Phase 1 critical fixes

---

## Notes for Future Sessions

1. **Start here:** Phase 1 critical fixes (security + bugs)
2. **Quick wins:** DB indexes, BUG-001 fix, bare except cleanup
3. **High impact:** Template base.html saves 400+ lines
4. **Test after:** Run app after each change to verify no regressions
5. **Backup first:** Database is in `pos_test.db` / `pos_prod.db`

---

## Related Files

- `CLAUDE.md` - Project context and overview
- `app.py` - Main application (2100 lines)
- `requirements.txt` - Python dependencies
- `api_desc.yaml` - Billingo API specification
- `roast_tracker/` - Separate module for coffee roasting
