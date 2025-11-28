"""
Database setup and utilities for Roast Tracker
"""
import sqlite3
import os

DATABASE_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'roast_tracker.db')


def get_db():
    """Get database connection"""
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def query_db(query, args=(), one=False):
    """Execute a query and return results"""
    conn = get_db()
    cur = conn.execute(query, args)
    rv = cur.fetchall()
    conn.commit()
    conn.close()
    return (rv[0] if rv else None) if one else rv


def init_db():
    """Initialize the database with schema"""
    conn = get_db()
    cur = conn.cursor()

    # Green coffee (raw materials)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS green_coffee (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            country TEXT NOT NULL,
            region TEXT,
            process TEXT,
            variety TEXT,
            altitude TEXT,
            tasting_notes TEXT,
            current_stock_kg REAL DEFAULT 0,
            supplier TEXT,
            purchase_date DATE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Coffee products (roasted coffee types/recipes)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS coffee_products (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            green_coffee_id INTEGER,
            roast_level TEXT NOT NULL CHECK(roast_level IN ('V', 'K', 'S')),
            description TEXT,
            target_drop_temp REAL,
            target_roast_time INTEGER,
            is_active INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (green_coffee_id) REFERENCES green_coffee(id)
        )
    """)

    # Roast batches
    cur.execute("""
        CREATE TABLE IF NOT EXISTS roast_batches (
            id INTEGER PRIMARY KEY,
            lot_number TEXT NOT NULL UNIQUE,
            product_id INTEGER NOT NULL,
            roast_date DATE NOT NULL,
            roast_level TEXT NOT NULL CHECK(roast_level IN ('V', 'K', 'S')),
            day_sequence INTEGER NOT NULL,
            green_weight_g REAL NOT NULL,
            roasted_weight_g REAL NOT NULL,
            available_weight_g REAL NOT NULL,
            weight_loss_percent REAL,
            roasttime_uid TEXT,
            preheat_temp REAL,
            charge_temp REAL,
            first_crack_time INTEGER,
            first_crack_temp REAL,
            drop_temp REAL,
            total_roast_time INTEGER,
            ambient_temp REAL,
            humidity REAL,
            notes TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (product_id) REFERENCES coffee_products(id)
        )
    """)

    # Storage containers
    cur.execute("""
        CREATE TABLE IF NOT EXISTS storage_containers (
            id INTEGER PRIMARY KEY,
            container_code TEXT NOT NULL,
            roast_batch_id INTEGER,
            weight_g REAL DEFAULT 0,
            location TEXT,
            status TEXT DEFAULT 'active' CHECK(status IN ('active', 'empty', 'archived')),
            filled_at TIMESTAMP,
            emptied_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (roast_batch_id) REFERENCES roast_batches(id)
        )
    """)

    # Production batches (packaging runs)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS production_batches (
            id INTEGER PRIMARY KEY,
            production_lot TEXT NOT NULL,
            production_type TEXT NOT NULL CHECK(production_type IN (
                'whole_bean_16', 'whole_bean_70', 'whole_bean_250',
                'drip_11', 'cold_brew', 'market', 'sampling', 'advent'
            )),
            package_size_g INTEGER,
            quantity INTEGER NOT NULL,
            total_coffee_used_g REAL NOT NULL,
            production_date DATE NOT NULL,
            produced_by TEXT,
            notes TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Links production batches to source roast batches (many-to-many)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS production_sources (
            id INTEGER PRIMARY KEY,
            production_batch_id INTEGER NOT NULL,
            roast_batch_id INTEGER NOT NULL,
            weight_used_g REAL NOT NULL,
            FOREIGN KEY (production_batch_id) REFERENCES production_batches(id),
            FOREIGN KEY (roast_batch_id) REFERENCES roast_batches(id)
        )
    """)

    # Advent calendar contents (24 days, each from different LOT)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS advent_calendar_contents (
            id INTEGER PRIMARY KEY,
            advent_lot TEXT NOT NULL,
            calendar_year INTEGER NOT NULL,
            day_number INTEGER NOT NULL CHECK(day_number BETWEEN 1 AND 24),
            roast_batch_id INTEGER NOT NULL,
            weight_g REAL NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (roast_batch_id) REFERENCES roast_batches(id),
            UNIQUE(advent_lot, day_number)
        )
    """)

    # Roast planning
    cur.execute("""
        CREATE TABLE IF NOT EXISTS roast_plans (
            id INTEGER PRIMARY KEY,
            planned_date DATE NOT NULL,
            product_id INTEGER NOT NULL,
            planned_green_weight_g REAL NOT NULL,
            status TEXT DEFAULT 'planned' CHECK(status IN ('planned', 'completed', 'cancelled')),
            roast_batch_id INTEGER,
            notes TEXT,
            source TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (product_id) REFERENCES coffee_products(id),
            FOREIGN KEY (roast_batch_id) REFERENCES roast_batches(id)
        )
    """)

    # Advent calendar configuration (which coffees are currently configured)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS advent_calendar_config (
            id INTEGER PRIMARY KEY,
            slot_number INTEGER NOT NULL CHECK(slot_number BETWEEN 1 AND 8),
            roast_type TEXT NOT NULL CHECK(roast_type IN ('light', 'medium')),
            product_id INTEGER NOT NULL,
            is_active INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (product_id) REFERENCES coffee_products(id),
            UNIQUE(slot_number, roast_type)
        )
    """)

    # Inventory adjustments (audit trail for manual inventory changes)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS inventory_adjustments (
            id INTEGER PRIMARY KEY,
            product_id INTEGER NOT NULL,
            batch_id INTEGER,
            adjustment_type TEXT NOT NULL CHECK(adjustment_type IN ('add', 'subtract', 'set', 'correction')),
            amount_g REAL NOT NULL,
            previous_total_g REAL NOT NULL,
            new_total_g REAL NOT NULL,
            comment TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (product_id) REFERENCES coffee_products(id),
            FOREIGN KEY (batch_id) REFERENCES roast_batches(id)
        )
    """)

    # Order LOT assignments (links WooCommerce order items to roast batch LOTs)
    # For 500g orders, we need 2 LOT entries (2x250g), so slot_number tracks position
    cur.execute("""
        CREATE TABLE IF NOT EXISTS order_lot_assignments (
            id INTEGER PRIMARY KEY,
            wc_order_id INTEGER NOT NULL,
            wc_order_item_id INTEGER NOT NULL,
            slot_number INTEGER NOT NULL DEFAULT 1,
            roast_batch_id INTEGER NOT NULL,
            weight_g REAL NOT NULL DEFAULT 250,
            assigned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (roast_batch_id) REFERENCES roast_batches(id)
        )
    """)

    # Create indexes
    cur.execute("CREATE INDEX IF NOT EXISTS idx_roast_batches_lot ON roast_batches(lot_number)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_roast_batches_date ON roast_batches(roast_date)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_roast_batches_product ON roast_batches(product_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_production_date ON production_batches(production_date)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_inventory_adjustments_product ON inventory_adjustments(product_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_inventory_adjustments_date ON inventory_adjustments(created_at)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_order_lot_assignments_order ON order_lot_assignments(wc_order_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_order_lot_assignments_item ON order_lot_assignments(wc_order_item_id)")

    conn.commit()
    conn.close()
    print(f"Database initialized at {DATABASE_PATH}")


if __name__ == "__main__":
    init_db()
