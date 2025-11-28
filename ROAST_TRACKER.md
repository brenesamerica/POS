# Roast Tracker & Production Management System

## Project Overview

A system to track coffee roasting, storage, and packaging production for Café Tiko. Integrates with the existing POS system and potentially WooCommerce orders.

## Business Context

### Raw Material (Green Coffee)
- Source country
- Process type (washed, natural, honey, etc.)
- Tasting notes
- Each country has 3 roast profiles (products)

### Roast Levels & Products
| Level | Code | Description |
|-------|------|-------------|
| Light | V (Világos) | Light roast |
| Medium | K (Közepes) | Medium roast |
| Dark | S (Sötét) | Dark roast |

### LOT Number System

#### Roasted Coffee LOT
Format: `X/YEARMONTHDAY/Y`
- **X**: Roast level (V, K, or S)
- **YEAR**: 4 digits (2025)
- **MONTH**: Hungarian abbreviation (JAN, FEB, MÁR, ÁPR, MÁJ, JÚN, JÚL, AUG, SZEPT, OKT, NOV, DEC)
- **DAY**: 2 digits (01-31)
- **Y**: Sequential number for that roast level on that day

Example: `V/2025NOV05/1` = First light roast on November 5, 2025

**Rule**: If same roast level + same coffee on same day = same LOT number

#### Product LOT Numbers
| Product Type | LOT Format | Notes |
|--------------|------------|-------|
| Whole Bean (16g, 70g, 250g) | Same as roast LOT | `V/2025NOV05/1` |
| Drip Coffee (11g) | `TG/X/YEARMONTHDAY/Y` | TG prefix |
| Advent Calendar | `AK/YEARMONTHDAY/Y` | Multi-LOT selection |
| Cold Brew | `CB/YEARMONTHDAY/Y` | Usually Y=1 |

### Package Types
| Type | Weight | Use Case |
|------|--------|----------|
| Sampling | Variable | QC testing |
| Mini | 16g | Single serve |
| Small | 70g | Trial size |
| Standard | 250g | Retail |
| Drip | 11g | Drip coffee bags |
| Market | Variable | Manual entry |
| Cold Brew | Variable | Manual entry |

## Features

### Phase 1: Core Tracking ✅ IN PROGRESS
- [ ] Database schema for raw materials, roasts, storage, production
- [ ] LOT number generation logic
- [ ] Roast entry screen (manual + import from RoastTime)
- [ ] Storage container tracking
- [ ] Production/packaging screen
- [ ] Inventory dashboard

### Phase 2: Planning & Orders
- [ ] Roast planning based on inventory
- [ ] WooCommerce order integration
- [ ] Order → LOT number matching
- [ ] Production scheduling

### Phase 3: Reporting & Analytics
- [ ] Production reports
- [ ] Traceability reports (LOT → customer)
- [ ] Inventory forecasting
- [ ] Roast profile analytics (from RoastTime data)

## Database Schema

### Tables

```sql
-- Raw materials (green coffee)
CREATE TABLE green_coffee (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    country TEXT NOT NULL,
    region TEXT,
    process TEXT,  -- washed, natural, honey, etc.
    tasting_notes TEXT,
    current_stock_kg REAL DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Products (roasted coffee types)
CREATE TABLE coffee_products (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,  -- e.g., "Kukulkan", "Oromo"
    green_coffee_id INTEGER,
    roast_level TEXT NOT NULL,  -- V, K, S
    description TEXT,
    is_active INTEGER DEFAULT 1,
    FOREIGN KEY (green_coffee_id) REFERENCES green_coffee(id)
);

-- Roast batches
CREATE TABLE roast_batches (
    id INTEGER PRIMARY KEY,
    lot_number TEXT NOT NULL UNIQUE,
    product_id INTEGER NOT NULL,
    roast_date DATE NOT NULL,
    roast_level TEXT NOT NULL,  -- V, K, S
    day_sequence INTEGER NOT NULL,  -- Y in LOT number
    green_weight_g REAL NOT NULL,
    roasted_weight_g REAL NOT NULL,
    available_weight_g REAL NOT NULL,  -- Decreases as used
    roasttime_uid TEXT,  -- Link to RoastTime file
    first_crack_time INTEGER,
    total_roast_time INTEGER,
    drop_temperature REAL,
    notes TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (product_id) REFERENCES coffee_products(id)
);

-- Storage containers
CREATE TABLE storage_containers (
    id INTEGER PRIMARY KEY,
    container_code TEXT NOT NULL,
    roast_batch_id INTEGER,
    current_weight_g REAL DEFAULT 0,
    location TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (roast_batch_id) REFERENCES roast_batches(id)
);

-- Production batches (packaging)
CREATE TABLE production_batches (
    id INTEGER PRIMARY KEY,
    production_lot TEXT NOT NULL,
    production_type TEXT NOT NULL,  -- whole_bean, drip, cold_brew, advent
    package_size_g INTEGER,  -- 16, 70, 250, 11, or NULL for variable
    quantity INTEGER NOT NULL,
    total_coffee_used_g REAL NOT NULL,
    production_date DATE NOT NULL,
    notes TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Links production to source roast batches
CREATE TABLE production_sources (
    id INTEGER PRIMARY KEY,
    production_batch_id INTEGER NOT NULL,
    roast_batch_id INTEGER NOT NULL,
    weight_used_g REAL NOT NULL,
    FOREIGN KEY (production_batch_id) REFERENCES production_batches(id),
    FOREIGN KEY (roast_batch_id) REFERENCES roast_batches(id)
);

-- Advent calendar contents
CREATE TABLE advent_calendar_contents (
    id INTEGER PRIMARY KEY,
    advent_lot TEXT NOT NULL,  -- AK/YEARMONTHDAY/Y
    day_number INTEGER NOT NULL,  -- 1-24
    roast_batch_id INTEGER NOT NULL,
    weight_g REAL NOT NULL,
    FOREIGN KEY (roast_batch_id) REFERENCES roast_batches(id)
);
```

## Hungarian Month Codes
```python
MONTH_CODES = {
    1: 'JAN', 2: 'FEB', 3: 'MÁR', 4: 'ÁPR',
    5: 'MÁJ', 6: 'JÚN', 7: 'JÚL', 8: 'AUG',
    9: 'SZEPT', 10: 'OKT', 11: 'NOV', 12: 'DEC'
}
```

## UI Screens

### 1. Dashboard
- Available roasted coffee by LOT
- Low stock alerts
- Recent production

### 2. Roast Entry
- Manual entry or import from RoastTime
- Auto-generate LOT number
- Link to green coffee source

### 3. Production Screen
- Select roast LOT → Choose production type
- Whole Bean: Select package size (16g, 70g, 250g) → Enter quantity
- Drip: 11g fixed → Enter quantity
- Cold Brew: Enter weight used
- Market: Enter weight used

### 4. Advent Calendar
- Select date → Generate AK/YEARMONTHDAY/Y
- Pick 24 LOTs from available V and K roasts
- Track weights used from each

### 5. Inventory
- Current stock by LOT
- Production history per LOT
- Traceability view

## File Structure
```
POS/
├── roast_tracker/
│   ├── __init__.py
│   ├── models.py          # Database models
│   ├── lot_generator.py   # LOT number logic
│   ├── roasttime_import.py # Import from RoastTime
│   └── routes.py          # Flask routes
├── templates/
│   └── roast_tracker/
│       ├── dashboard.html
│       ├── roast_entry.html
│       ├── production.html
│       ├── advent_calendar.html
│       └── inventory.html
└── roast_tracker.db       # Separate database
```

## Development Progress

### 2025-11-28: Project Started
- [x] Created project specification (this file)
- [x] Defined LOT number system
- [x] Designed database schema
- [ ] Create feature branch
- [ ] Implement database schema
- [ ] Build LOT number generator
- [ ] Create basic UI

## Notes
- RoastTime data location: `C:\Users\brene\AppData\Roaming\roast-time\roasts\`
- 488 historical roasts available for import
- Consider separate Flask Blueprint or even separate app
