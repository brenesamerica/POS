from flask import Flask, request, jsonify, render_template, redirect, url_for, send_file, session, Response, g
import sqlite3
import requests
import logging
import io
import os
import json
import time
import hashlib
import secrets
from functools import wraps
from datetime import datetime, timedelta


app = Flask(__name__)

app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-secret-key-change-in-production")

# Register Roast Tracker Blueprint
from roast_tracker.routes import roast_tracker
app.register_blueprint(roast_tracker)

# Custom Jinja2 filter to parse JSON
@app.template_filter('from_json')
def from_json_filter(value):
    """Parse JSON string to dict"""
    if value:
        try:
            return json.loads(value)
        except:
            return {}
    return {}

# Session configuration
SESSION_TIMEOUT_MINUTES = 15
# Set up logging for debugging
logging.basicConfig(level=logging.DEBUG)

# Billingo API settings
# Environment: 'test' or 'prod'
BILLINGO_ENV = os.environ.get("BILLINGO_ENV", "test")  # Default to test for safety

BILLINGO_API_KEYS = {
    "test": "dc7626a6-9ed2-11ef-9815-0254eb6072a0",
    "prod": "75757e6a-a040-11ef-b7e8-06ac9760f844"
}

# Block IDs for each environment
# Receipt blocks (nyugtatömb)
BILLINGO_RECEIPT_BLOCK_IDS = {
    "test": int(os.environ.get("BILLINGO_RECEIPT_BLOCK_ID_TEST", "262126")),
    "prod": int(os.environ.get("BILLINGO_RECEIPT_BLOCK_ID_PROD", "233585"))
}
# Invoice blocks (számlatömb)
BILLINGO_INVOICE_BLOCK_IDS = {
    "test": int(os.environ.get("BILLINGO_INVOICE_BLOCK_ID_TEST", "112373")),
    "prod": int(os.environ.get("BILLINGO_INVOICE_BLOCK_ID_PROD", "117779"))
}

BILLINGO_API_KEY = BILLINGO_API_KEYS.get(BILLINGO_ENV, BILLINGO_API_KEYS["test"])
BILLINGO_RECEIPT_BLOCK_ID = BILLINGO_RECEIPT_BLOCK_IDS.get(BILLINGO_ENV, BILLINGO_RECEIPT_BLOCK_IDS["prod"])
BILLINGO_INVOICE_BLOCK_ID = BILLINGO_INVOICE_BLOCK_IDS.get(BILLINGO_ENV, BILLINGO_INVOICE_BLOCK_IDS["prod"])
BILLINGO_BASE_URL = "https://api.billingo.hu/v3"

# Database: Use separate databases for test and prod environments
DATABASE = 'pos_test.db' if BILLINGO_ENV == 'test' else 'pos_prod.db'

logging.info(f"Billingo environment: {BILLINGO_ENV}, Receipt Block: {BILLINGO_RECEIPT_BLOCK_ID}, Invoice Block: {BILLINGO_INVOICE_BLOCK_ID}")
logging.info(f"Using database: {DATABASE}")

# In-memory storage for active sales per user (for customer display)
# Format: { 'username': { 'items': [...], 'total': 0, 'updated_at': timestamp } }
active_sales = {}

# Database helper functions
def query_db(query, args=(), one=False):
    conn = sqlite3.connect(DATABASE)
    cur = conn.cursor()
    cur.execute(query, args)
    rv = cur.fetchall()
    conn.commit()
    conn.close()
    return (rv[0] if rv else None) if one else rv

def init_db():
    with sqlite3.connect(DATABASE) as conn:
        cur = conn.cursor()
        # Create categories table with source field
        # source: 'woocommerce' for imported, 'manual' for manually added
        cur.execute("""
            CREATE TABLE IF NOT EXISTS categories (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                is_coffee_shop INTEGER DEFAULT 0,
                source TEXT DEFAULT 'manual'
            )
        """)
        # Create items table with source field
        cur.execute("""
            CREATE TABLE IF NOT EXISTS items (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                price REAL,
                vat TEXT DEFAULT '27%',
                category_id INTEGER,
                image_url TEXT,
                description TEXT,
                attributes TEXT,
                source TEXT DEFAULT 'manual',
                FOREIGN KEY (category_id) REFERENCES categories (id)
            )
        """)

        # Migration: Add source column if it doesn't exist
        cur.execute("PRAGMA table_info(categories)")
        cat_columns = [col[1] for col in cur.fetchall()]
        if 'source' not in cat_columns:
            cur.execute("ALTER TABLE categories ADD COLUMN source TEXT DEFAULT 'manual'")

        cur.execute("PRAGMA table_info(items)")
        item_columns = [col[1] for col in cur.fetchall()]
        if 'source' not in item_columns:
            cur.execute("ALTER TABLE items ADD COLUMN source TEXT DEFAULT 'manual'")
        # Create market sessions table
        cur.execute("""
            CREATE TABLE IF NOT EXISTS market_sessions (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                closed_at TIMESTAMP
            )
        """)
        # Create market session items table (same product can have multiple LOT numbers)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS market_session_items (
                id INTEGER PRIMARY KEY,
                session_id INTEGER NOT NULL,
                item_id INTEGER NOT NULL,
                lot_number TEXT NOT NULL,
                quantity_prepared INTEGER NOT NULL,
                quantity_remaining INTEGER NOT NULL,
                FOREIGN KEY (session_id) REFERENCES market_sessions (id),
                FOREIGN KEY (item_id) REFERENCES items (id)
            )
        """)
        # Create market sales table to track individual sales
        cur.execute("""
            CREATE TABLE IF NOT EXISTS market_sales (
                id INTEGER PRIMARY KEY,
                session_id INTEGER NOT NULL,
                sale_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                total_amount REAL NOT NULL,
                payment_method TEXT NOT NULL,
                items_json TEXT,
                receipt_id TEXT,
                FOREIGN KEY (session_id) REFERENCES market_sessions (id)
            )
        """)
        # Migration: add is_coffee_shop column if it doesn't exist
        cur.execute("PRAGMA table_info(categories)")
        columns = [col[1] for col in cur.fetchall()]
        if 'is_coffee_shop' not in columns:
            cur.execute("ALTER TABLE categories ADD COLUMN is_coffee_shop INTEGER DEFAULT 0")
        # Migration: add initial_cash column to market_sessions
        cur.execute("PRAGMA table_info(market_sessions)")
        columns = [col[1] for col in cur.fetchall()]
        if 'initial_cash' not in columns:
            cur.execute("ALTER TABLE market_sessions ADD COLUMN initial_cash REAL DEFAULT 0")

        # Migration: add sold_by column to market_sales
        cur.execute("PRAGMA table_info(market_sales)")
        columns = [col[1] for col in cur.fetchall()]
        if 'sold_by' not in columns:
            cur.execute("ALTER TABLE market_sales ADD COLUMN sold_by TEXT")

        # Migration: add cancelled column to market_sales
        cur.execute("PRAGMA table_info(market_sales)")
        columns = [col[1] for col in cur.fetchall()]
        if 'cancelled' not in columns:
            cur.execute("ALTER TABLE market_sales ADD COLUMN cancelled INTEGER DEFAULT 0")

        # Create users table for authentication
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                salt TEXT NOT NULL,
                role TEXT DEFAULT 'user',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_login TIMESTAMP
            )
        """)

        # Create default admin user if no users exist
        cur.execute("SELECT COUNT(*) FROM users")
        if cur.fetchone()[0] == 0:
            # Default admin: username=admin, password=admin (should be changed immediately!)
            salt = secrets.token_hex(32)
            password_hash = hashlib.sha256(('admin' + salt).encode()).hexdigest()
            cur.execute("""
                INSERT INTO users (username, password_hash, salt, role)
                VALUES (?, ?, ?, 'admin')
            """, ('admin', password_hash, salt))
            logging.info("Created default admin user (username: admin, password: admin)")

        conn.commit()


# Password hashing functions
def hash_password(password, salt=None):
    """Hash a password with a salt"""
    if salt is None:
        salt = secrets.token_hex(32)
    password_hash = hashlib.sha256((password + salt).encode()).hexdigest()
    return password_hash, salt


def verify_password(password, password_hash, salt):
    """Verify a password against its hash"""
    return hashlib.sha256((password + salt).encode()).hexdigest() == password_hash


# Authentication decorators
def login_required(f):
    """Decorator to require login for a route"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            if request.is_json:
                return jsonify({"status": "error", "message": "Authentication required"}), 401
            return redirect(url_for('login', next=request.url))

        # Check session expiration
        if 'last_activity' in session:
            last_activity = datetime.fromisoformat(session['last_activity'])
            if datetime.now() - last_activity > timedelta(minutes=SESSION_TIMEOUT_MINUTES):
                session.clear()
                if request.is_json:
                    return jsonify({"status": "error", "message": "Session expired"}), 401
                return redirect(url_for('login', next=request.url))

        # Update last activity (sliding expiration)
        session['last_activity'] = datetime.now().isoformat()
        return f(*args, **kwargs)
    return decorated_function


def admin_required(f):
    """Decorator to require admin role for a route"""
    @wraps(f)
    @login_required
    def decorated_function(*args, **kwargs):
        if session.get('role') != 'admin':
            if request.is_json:
                return jsonify({"status": "error", "message": "Admin access required"}), 403
            return render_template('error.html', message="Admin access required"), 403
        return f(*args, **kwargs)
    return decorated_function

# WooCommerce API settings
WOOCOMMERCE_URL = 'https://cafetiko.com'
WOOCOMMERCE_CONSUMER_KEY = 'ck_7a1052542a4c7a0470a8f2d4cfc65c47ea0cbfe7'
WOOCOMMERCE_CONSUMER_SECRET = 'cs_9e74a7cac66169b23daf4594f7868bedae810b9f'

def wc_api_request(endpoint, params=None):
    """Make a request to WooCommerce REST API"""
    url = f"{WOOCOMMERCE_URL}/wp-json/wc/v3/{endpoint}"
    auth = (WOOCOMMERCE_CONSUMER_KEY, WOOCOMMERCE_CONSUMER_SECRET)

    try:
        response = requests.get(url, auth=auth, params=params, timeout=30)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        logging.error(f"WooCommerce API error: {e}")
        return None


def fetch_wc_orders(status='processing', per_page=100):
    """Fetch WooCommerce orders with a specific status"""
    orders = []
    page = 1

    while True:
        params = {
            'page': page,
            'per_page': per_page,
            'status': status,
            'orderby': 'date',
            'order': 'desc'
        }

        data = wc_api_request('orders', params)

        if not data:
            break

        if len(data) == 0:
            break

        for order in data:
            order_data = {
                'id': order.get('id'),
                'number': order.get('number'),
                'status': order.get('status'),
                'date_created': order.get('date_created'),
                'total': order.get('total'),
                'currency': order.get('currency'),
                'billing': {
                    'first_name': order.get('billing', {}).get('first_name', ''),
                    'last_name': order.get('billing', {}).get('last_name', ''),
                    'email': order.get('billing', {}).get('email', ''),
                    'country': order.get('billing', {}).get('country', '')
                },
                'shipping': {
                    'first_name': order.get('shipping', {}).get('first_name', ''),
                    'last_name': order.get('shipping', {}).get('last_name', ''),
                    'country': order.get('shipping', {}).get('country', '')
                },
                'line_items': []
            }

            for item in order.get('line_items', []):
                line_item = {
                    'id': item.get('id'),
                    'product_id': item.get('product_id'),
                    'variation_id': item.get('variation_id'),
                    'name': item.get('name'),
                    'quantity': item.get('quantity'),
                    'subtotal': item.get('subtotal'),
                    'sku': item.get('sku'),
                    'meta_data': item.get('meta_data', [])
                }
                order_data['line_items'].append(line_item)

            orders.append(order_data)

        if len(data) < per_page:
            break

        page += 1

    logging.info(f"Fetched {len(orders)} orders with status '{status}'")
    return orders

def fetch_products(lang=None):
    """Fetch all products from WooCommerce, including variations"""
    products = []
    page = 1
    per_page = 100

    while True:
        params = {'page': page, 'per_page': per_page, 'status': 'publish'}
        # Add language parameter if specified (for WPML compatibility)
        if lang:
            params['lang'] = lang

        data = wc_api_request('products', params)

        if not data:
            break

        if len(data) == 0:
            break

        for product in data:
            product_type = product.get('type', 'simple')

            if product_type == 'variable':
                # Fetch variations for variable products
                variations = fetch_product_variations(product, lang)
                products.extend(variations)
                logging.debug(f"Fetched {len(variations)} variations for: {product.get('name')}")
            else:
                # Simple product
                product_details = parse_wc_product(product)
                if product_details:
                    products.append(product_details)
                    logging.debug(f"Fetched product: {product_details['name']}")

        if len(data) < per_page:
            break

        page += 1

    logging.info(f"Fetched {len(products)} products from WooCommerce (lang={lang})")
    return products

def fetch_product_variations(parent_product, lang=None):
    """Fetch all variations of a variable product"""
    variations = []
    parent_id = parent_product.get('id')
    parent_name = parent_product.get('name', '')
    parent_categories = parent_product.get('categories', [])
    parent_images = parent_product.get('images', [])
    parent_description = parent_product.get('short_description', '') or parent_product.get('description', '')
    parent_attributes = parent_product.get('attributes', [])

    page = 1
    per_page = 100

    while True:
        params = {'page': page, 'per_page': per_page}
        if lang:
            params['lang'] = lang
        data = wc_api_request(f'products/{parent_id}/variations', params)

        if not data:
            break

        if len(data) == 0:
            break

        for variation in data:
            variation_details = parse_wc_variation(variation, parent_name, parent_categories, parent_images, parent_description, parent_attributes)
            if variation_details:
                variations.append(variation_details)

        if len(data) < per_page:
            break

        page += 1

    return variations

def parse_wc_variation(variation, parent_name, parent_categories, parent_images, parent_description, parent_attributes=None):
    """Parse a WooCommerce variation into our format"""
    try:
        if variation.get('status') != 'publish':
            return None

        variation_id = variation.get('id')

        # Build variation name from attributes
        attributes = variation.get('attributes', [])
        attr_names = [attr.get('option', '') for attr in attributes]
        variation_suffix = ' - ' + ', '.join(attr_names) if attr_names else ''
        name = f"{parent_name}{variation_suffix}"

        # Get price
        price_str = variation.get('price', '0')
        if not price_str:
            price_str = variation.get('regular_price', '0')
        price = float(price_str) if price_str else 0

        # Use parent category
        category_id = parent_categories[0]['id'] if parent_categories else None

        if not category_id:
            return None

        # Get variation image or fall back to parent
        variation_image = variation.get('image', {})
        if variation_image and variation_image.get('src'):
            image_url = variation_image.get('src')
        elif parent_images:
            image_url = parent_images[0]['src']
        else:
            image_url = ''

        if not name or price <= 0:
            return None

        # Extract product attributes (Origin, Roast, Process, etc.) from parent
        product_attrs = extract_product_attributes(parent_attributes or [])

        return {
            'id': variation_id,
            'name': name,
            'price': price,
            'category_id': category_id,
            'description': parent_description,
            'image_url': image_url,
            'attributes': product_attrs
        }
    except Exception as e:
        logging.error(f"Error parsing variation: {e}")
        return None

def extract_product_attributes(wc_attributes):
    """Extract relevant product attributes (Origin, Roast, Process, etc.)"""
    attrs = {}
    # List of attribute names we're interested in (case-insensitive matching)
    interesting_attrs = ['origin', 'roast', 'process', 'variety', 'altitude', 'region', 'farm', 'producer']

    for attr in wc_attributes:
        attr_name = attr.get('name', '').lower()
        for target in interesting_attrs:
            if target in attr_name:
                # Get the options/values
                options = attr.get('options', [])
                if options:
                    attrs[target.capitalize()] = ', '.join(options) if isinstance(options, list) else str(options)
                elif attr.get('option'):
                    attrs[target.capitalize()] = attr.get('option')
                break

    return attrs

def parse_wc_product(product):
    """Parse a WooCommerce simple product into our format"""
    try:
        # Skip if not published or not purchasable
        if product.get('status') != 'publish':
            return None

        product_id = product.get('id')
        name = product.get('name', '')

        # Get price (WooCommerce prices are already with tax based on settings)
        price_str = product.get('price', '0')
        if not price_str:
            price_str = product.get('regular_price', '0')
        price = float(price_str) if price_str else 0

        # Get category (use first category)
        categories = product.get('categories', [])
        category_id = categories[0]['id'] if categories else None

        if not category_id:
            logging.debug(f"No category for product {product_id}. Skipping.")
            return None

        # Get description
        description = product.get('short_description', '') or product.get('description', '')

        # Get image URL
        images = product.get('images', [])
        image_url = images[0]['src'] if images else ''

        # Extract product attributes (Origin, Roast, Process, etc.)
        product_attrs = extract_product_attributes(product.get('attributes', []))

        if not name or price <= 0:
            logging.debug(f"Missing name or invalid price for product {product_id}. Skipping.")
            return None

        return {
            'id': product_id,
            'name': name,
            'price': price,
            'category_id': category_id,
            'description': description,
            'image_url': image_url,
            'attributes': product_attrs
        }
    except Exception as e:
        logging.error(f"Error parsing product: {e}")
        return None

def fetch_categories_with_products(lang=None):
    """Fetch all categories from WooCommerce that have products"""
    categories = []
    page = 1
    per_page = 100

    while True:
        params = {'page': page, 'per_page': per_page, 'hide_empty': True}
        if lang:
            params['lang'] = lang
        data = wc_api_request('products/categories', params)

        if not data:
            break

        if len(data) == 0:
            break

        for category in data:
            categories.append({
                'id': category.get('id'),
                'name': category.get('name', '')
            })
            logging.debug(f"Fetched category: {category.get('name')}")

        if len(data) < per_page:
            break

        page += 1

    logging.info(f"Fetched {len(categories)} categories from WooCommerce (lang={lang})")
    return categories

@app.route('/update_catalog', methods=['POST'])
@login_required
def update_catalog_route():
    try:
        # Get languages from request (default to HU only)
        data = request.get_json() or {}
        languages = data.get('languages', ['hu'])

        # Validate languages
        valid_languages = ['hu', 'en']
        languages = [lang for lang in languages if lang in valid_languages]
        if not languages:
            languages = ['hu']

        result = update_catalog(languages)
        return jsonify({
            "status": "success",
            "message": "Catalog updated successfully",
            "summary": result
        })
    except Exception as e:
        logging.error("Error updating catalog: %s", e)
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/update_catalog_stream')
@login_required
def update_catalog_stream():
    """Stream catalog update progress using Server-Sent Events"""
    languages = request.args.getlist('lang') or ['hu']
    valid_languages = ['hu', 'en']
    languages = [lang for lang in languages if lang in valid_languages]
    if not languages:
        languages = ['hu']

    def generate():
        try:
            yield f"data: {json.dumps({'type': 'start', 'message': 'Starting catalog update...'})}\n\n"

            all_products = []
            all_categories = []
            summary = {
                'languages': languages,
                'categories_per_lang': {},
                'products_per_lang': {},
                'total_categories': 0,
                'total_products': 0,
                'products_with_attributes': 0
            }

            # Fetch products and categories for each language
            for lang in languages:
                yield f"data: {json.dumps({'type': 'status', 'message': f'Fetching categories for {lang.upper()}...'})}\n\n"
                categories = fetch_categories_with_products(lang)
                summary['categories_per_lang'][lang] = len(categories)
                all_categories.extend(categories)
                yield f"data: {json.dumps({'type': 'progress', 'message': f'Fetched {len(categories)} categories for {lang.upper()}'})}\n\n"

                yield f"data: {json.dumps({'type': 'status', 'message': f'Fetching products for {lang.upper()}...'})}\n\n"
                products = fetch_products(lang)
                summary['products_per_lang'][lang] = len(products)
                all_products.extend(products)
                yield f"data: {json.dumps({'type': 'progress', 'message': f'Fetched {len(products)} products for {lang.upper()}'})}\n\n"

            # Deduplicate by ID
            unique_products = {}
            for product in all_products:
                if product and product.get('id'):
                    unique_products[product['id']] = product

            unique_categories = {}
            for category in all_categories:
                if category and category.get('id'):
                    unique_categories[category['id']] = category

            products = list(unique_products.values())
            categories = list(unique_categories.values())

            yield f"data: {json.dumps({'type': 'status', 'message': 'Saving to database...'})}\n\n"

            with sqlite3.connect(DATABASE) as conn:
                cursor = conn.cursor()

                # Only delete WooCommerce-imported data
                cursor.execute("DELETE FROM categories WHERE source = 'woocommerce'")
                cursor.execute("DELETE FROM items WHERE source = 'woocommerce'")

                # Get active category IDs
                active_category_ids = set()
                for product in products:
                    if product and product.get('category_id'):
                        active_category_ids.add(product['category_id'])

                # Insert categories
                categories_inserted = 0
                for category in categories:
                    if category['id'] in active_category_ids:
                        cursor.execute("""
                            INSERT OR REPLACE INTO categories (id, name, source)
                            VALUES (?, ?, 'woocommerce')
                        """, (category['id'], category['name']))
                        categories_inserted += 1

                yield f"data: {json.dumps({'type': 'progress', 'message': f'Imported {categories_inserted} categories'})}\n\n"

                # Insert products
                products_inserted = 0
                products_with_attrs = 0
                for product in products:
                    if product:
                        attrs_json = json.dumps(product.get('attributes', {})) if product.get('attributes') else None
                        if product.get('attributes'):
                            products_with_attrs += 1

                        cursor.execute("""
                            INSERT OR REPLACE INTO items (id, name, price, vat, category_id, image_url, description, attributes, source)
                            VALUES (?, ?, ?, '27%', ?, ?, ?, ?, 'woocommerce')
                        """, (product['id'], product['name'], product['price'], product['category_id'],
                              product['image_url'], product['description'], attrs_json))
                        products_inserted += 1

                        # Send progress every 10 products
                        if products_inserted % 10 == 0:
                            yield f"data: {json.dumps({'type': 'progress', 'message': f'Imported {products_inserted} products...'})}\n\n"

                conn.commit()

            summary['total_categories'] = categories_inserted
            summary['total_products'] = products_inserted
            summary['products_with_attributes'] = products_with_attrs

            yield f"data: {json.dumps({'type': 'complete', 'message': 'Catalog updated successfully!', 'summary': summary})}\n\n"

        except Exception as e:
            logging.error(f"Error in catalog stream: {e}")
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"

    return Response(generate(), mimetype='text/event-stream', headers={
        'Cache-Control': 'no-cache',
        'X-Accel-Buffering': 'no'
    })

def update_catalog(languages=None):
    """Update catalog from WooCommerce for specified languages"""
    if languages is None:
        languages = ['hu']

    all_products = []
    all_categories = []
    summary = {
        'languages': languages,
        'categories_per_lang': {},
        'products_per_lang': {},
        'total_categories': 0,
        'total_products': 0,
        'products_with_attributes': 0
    }

    # Fetch products and categories for each language
    for lang in languages:
        products = fetch_products(lang)
        categories = fetch_categories_with_products(lang)

        summary['products_per_lang'][lang] = len(products)
        summary['categories_per_lang'][lang] = len(categories)

        all_products.extend(products)
        all_categories.extend(categories)

    # Deduplicate by ID (in case same product appears in multiple languages)
    unique_products = {}
    for product in all_products:
        if product and product.get('id'):
            unique_products[product['id']] = product

    unique_categories = {}
    for category in all_categories:
        if category and category.get('id'):
            unique_categories[category['id']] = category

    products = list(unique_products.values())
    categories = list(unique_categories.values())

    with sqlite3.connect(DATABASE) as conn:
        cursor = conn.cursor()

        # Only delete WooCommerce-imported data, keep manually added items
        cursor.execute("DELETE FROM categories WHERE source = 'woocommerce'")
        cursor.execute("DELETE FROM items WHERE source = 'woocommerce'")
        logging.info("Cleared old WooCommerce imports (kept manual items)")

        # Get the set of category IDs that have active products
        active_category_ids = set()
        for product in products:
            if product and product.get('category_id'):
                active_category_ids.add(product['category_id'])

        # Insert only categories that have active products (mark as woocommerce source)
        categories_inserted = 0
        for category in categories:
            if category['id'] in active_category_ids:
                cursor.execute("""
                    INSERT OR REPLACE INTO categories (id, name, source)
                    VALUES (?, ?, 'woocommerce')
                """, (category['id'], category['name']))
                categories_inserted += 1
                logging.debug(f"Inserted category: {category}")
            else:
                logging.debug(f"Skipped category (no active products): {category}")

        # Insert products (mark as woocommerce source)
        products_inserted = 0
        products_with_attrs = 0
        for product in products:
            if product:  # Only insert if product details are valid
                # Serialize attributes to JSON
                attrs_json = json.dumps(product.get('attributes', {})) if product.get('attributes') else None
                if product.get('attributes'):
                    products_with_attrs += 1

                cursor.execute("""
                    INSERT OR REPLACE INTO items (id, name, price, vat, category_id, image_url, description, attributes, source)
                    VALUES (?, ?, ?, '27%', ?, ?, ?, ?, 'woocommerce')
                """, (product['id'], product['name'], product['price'], product['category_id'],
                      product['image_url'], product['description'], attrs_json))
                products_inserted += 1
                logging.debug(f"Inserted item: {product['name']}")

        conn.commit()

        summary['total_categories'] = categories_inserted
        summary['total_products'] = products_inserted
        summary['products_with_attributes'] = products_with_attrs

        logging.info(f"Catalog updated: {categories_inserted} categories, {products_inserted} products (langs: {languages})")

    return summary

@app.route('/verify_data', methods=['GET'])
@login_required
def verify_data():
    categories = query_db("SELECT * FROM categories")
    items = query_db("SELECT * FROM items")
    logging.debug(f"Categories in DB: {categories}")
    logging.debug(f"Items in DB: {items}")
    return jsonify({"categories": categories, "items": items})

# Initialize the database
init_db()

# Inject billingo_env and user info into all templates automatically
@app.context_processor
def inject_globals():
    return dict(
        billingo_env=BILLINGO_ENV,
        current_user=session.get('username'),
        is_admin=session.get('role') == 'admin'
    )


# ==================== AUTHENTICATION ROUTES ====================

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')

        if not username or not password:
            return render_template('login.html', error="Username and password are required")

        # Look up user
        user = query_db("SELECT id, username, password_hash, salt, role FROM users WHERE username = ?",
                       [username], one=True)

        if user and verify_password(password, user[2], user[3]):
            # Successful login
            session['user_id'] = user[0]
            session['username'] = user[1]
            session['role'] = user[4]
            session['last_activity'] = datetime.now().isoformat()

            # Update last login time
            query_db("UPDATE users SET last_login = CURRENT_TIMESTAMP WHERE id = ?", [user[0]])

            logging.info(f"User '{username}' logged in successfully")

            # Redirect to next page or main menu
            next_page = request.args.get('next')
            if next_page and next_page.startswith('/'):
                return redirect(next_page)
            return redirect(url_for('main_menu'))
        else:
            logging.warning(f"Failed login attempt for username: {username}")
            return render_template('login.html', error="Invalid username or password")

    return render_template('login.html')


@app.route('/logout')
def logout():
    username = session.get('username', 'Unknown')
    session.clear()
    logging.info(f"User '{username}' logged out")
    return redirect(url_for('login'))


# ==================== USER MANAGEMENT ROUTES (Admin Only) ====================

@app.route('/manage_users')
@admin_required
def manage_users():
    users = query_db("SELECT id, username, role, created_at, last_login FROM users ORDER BY username")
    return render_template('manage_users.html', users=users)


@app.route('/add_user', methods=['GET', 'POST'])
@admin_required
def add_user():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        confirm_password = request.form.get('confirm_password', '')
        role = request.form.get('role', 'user')

        # Validation
        if not username or not password:
            return render_template('add_user.html', error="Username and password are required")

        if password != confirm_password:
            return render_template('add_user.html', error="Passwords do not match")

        if len(password) < 4:
            return render_template('add_user.html', error="Password must be at least 4 characters")

        if role not in ['admin', 'user']:
            role = 'user'

        # Check if username exists
        existing = query_db("SELECT id FROM users WHERE username = ?", [username], one=True)
        if existing:
            return render_template('add_user.html', error="Username already exists")

        # Create user
        password_hash, salt = hash_password(password)
        query_db("INSERT INTO users (username, password_hash, salt, role) VALUES (?, ?, ?, ?)",
                (username, password_hash, salt, role))

        logging.info(f"Admin created new user: {username} (role: {role})")
        return redirect(url_for('manage_users'))

    return render_template('add_user.html')


@app.route('/edit_user/<int:id>', methods=['GET', 'POST'])
@admin_required
def edit_user(id):
    user = query_db("SELECT id, username, role FROM users WHERE id = ?", [id], one=True)
    if not user:
        return redirect(url_for('manage_users'))

    if request.method == 'POST':
        new_password = request.form.get('password', '')
        confirm_password = request.form.get('confirm_password', '')
        role = request.form.get('role', 'user')

        if role not in ['admin', 'user']:
            role = 'user'

        # Update role
        query_db("UPDATE users SET role = ? WHERE id = ?", (role, id))

        # Update password if provided
        if new_password:
            if new_password != confirm_password:
                return render_template('edit_user.html', user=user, error="Passwords do not match")
            if len(new_password) < 4:
                return render_template('edit_user.html', user=user, error="Password must be at least 4 characters")

            password_hash, salt = hash_password(new_password)
            query_db("UPDATE users SET password_hash = ?, salt = ? WHERE id = ?",
                    (password_hash, salt, id))

        logging.info(f"Admin updated user: {user[1]}")
        return redirect(url_for('manage_users'))

    return render_template('edit_user.html', user=user)


@app.route('/delete_user/<int:id>', methods=['POST'])
@admin_required
def delete_user(id):
    # Prevent deleting yourself
    if session.get('user_id') == id:
        return jsonify({"status": "error", "message": "Cannot delete your own account"}), 400

    user = query_db("SELECT username FROM users WHERE id = ?", [id], one=True)
    if user:
        query_db("DELETE FROM users WHERE id = ?", [id])
        logging.info(f"Admin deleted user: {user[0]}")
        return jsonify({"status": "success", "message": "User deleted successfully"})

    return jsonify({"status": "error", "message": "User not found"}), 404


# ==================== MAIN APPLICATION ROUTES ====================

@app.route('/')
@login_required
def main_menu():
    return render_template('main_menu.html')

@app.route('/manage_categories')
@login_required
def manage_categories():
    categories = query_db("SELECT * FROM categories")
    return render_template('manage_categories.html', categories=categories)

@app.route('/add_category', methods=['GET', 'POST'])
@login_required
def add_category():
    if request.method == 'POST':
        name = request.form.get('name')
        is_coffee_shop = 1 if request.form.get('is_coffee_shop') else 0
        query_db("INSERT INTO categories (name, is_coffee_shop, source) VALUES (?, ?, 'manual')", (name, is_coffee_shop))
        return redirect(url_for('manage_categories'))
    return render_template('add_category.html')

@app.route('/edit_category/<int:id>', methods=['GET', 'POST'])
@login_required
def edit_category(id):
    category = query_db("SELECT * FROM categories WHERE id = ?", [id], one=True)
    if request.method == 'POST':
        name = request.form.get('name')
        is_coffee_shop = 1 if request.form.get('is_coffee_shop') else 0
        query_db("UPDATE categories SET name = ?, is_coffee_shop = ? WHERE id = ?", (name, is_coffee_shop, id))
        return redirect(url_for('manage_categories'))
    return render_template('edit_category.html', category=category)

@app.route('/delete_category/<int:id>', methods=['POST'])
@login_required
def delete_category(id):
    query_db("DELETE FROM categories WHERE id = ?", [id])
    return jsonify({"status": "success", "message": "Category deleted successfully"})

@app.route('/manage_items')
@login_required
def manage_items():
    items = query_db("""
        SELECT items.id, items.name, items.price, items.vat, categories.name AS category_name, items.attributes
        FROM items
        LEFT JOIN categories ON items.category_id = categories.id
    """)
    categories = query_db("SELECT * FROM categories ORDER BY name")

    # Extract unique origins and roasts from attributes
    origins = set()
    roasts = set()
    for item in items:
        if item[5]:  # attributes column
            try:
                attrs = json.loads(item[5])
                if attrs.get('Origin'):
                    origins.add(attrs['Origin'])
                if attrs.get('Roast'):
                    roasts.add(attrs['Roast'])
            except:
                pass

    return render_template('manage_items.html',
                          items=items,
                          categories=categories,
                          origins=sorted(origins),
                          roasts=sorted(roasts))


@app.route('/add_item', methods=['GET', 'POST'])
@login_required
def add_item():
    categories = query_db("SELECT * FROM categories")
    if request.method == 'POST':
        name = request.form.get('name')
        price = float(request.form.get('price'))
        category_id = request.form.get('category_id')
        vat = request.form.get('vat', '27%')
        query_db("INSERT INTO items (name, price, vat, category_id, source) VALUES (?, ?, ?, ?, 'manual')", (name, price, vat, category_id))
        return redirect(url_for('manage_items'))
    return render_template('add_item.html', categories=categories)

@app.route('/edit_item/<int:id>', methods=['GET', 'POST'])
@login_required
def edit_item(id):
    item = query_db("SELECT * FROM items WHERE id = ?", [id], one=True)
    categories = query_db("SELECT * FROM categories")
    if request.method == 'POST':
        name = request.form.get('name')
        price = float(request.form.get('price'))
        category_id = request.form.get('category_ids')
        vat = request.form.get('vat', '27%')
        query_db("UPDATE items SET name = ?, price = ?, vat = ?, category_id = ? WHERE id = ?", (name, price, vat, category_id, id))
        return redirect(url_for('manage_items'))
    return render_template('edit_item.html', item=item, categories=categories)

@app.route('/delete_item/<int:id>', methods=['POST'])
@login_required
def delete_item(id):
    query_db("DELETE FROM items WHERE id = ?", [id])
    return jsonify({"status": "success", "message": "Item deleted successfully"})

@app.route('/create_receipt')
@login_required
def create_receipt_page():
    items = query_db("SELECT * FROM items")
    categories = query_db("SELECT * FROM categories")
    return render_template('create_receipt.html', items=items, categories=categories)



@app.route('/create_receipt', methods=['POST'])
@login_required
def create_receipt():
    try:
        # List of valid payment methods
        valid_payment_methods = [
            "aruhitel", "bankcard", "barion", "barter", "cash", "cash_on_delivery",
            "coupon", "elore_utalas", "ep_kartya", "kompenzacio", "levonas",
            "online_bankcard", "other", "paylike", "payoneer", "paypal", "paypal_utolag",
            "payu", "pick_pack_pont", "postai_csekk", "postautalvany", "skrill",
            "szep_card", "transferwise", "upwork", "utalvany", "valto", "wire_transfer"
        ]

        data = request.get_json()

        # Extract data from the request
        items = data.get('items', [])
        discount = float(data.get('discount', 0))
        payment_method = data.get('payment_method', 'cash')
        electronic = data.get('electronic', False)
        emails = data.get('emails', [])
        customer_name = data.get('name', '')

        # Validate the payment method
        if payment_method not in valid_payment_methods:
            return jsonify({
                "status": "error",
                "message": f"Invalid payment method: {payment_method}. Valid options are: {', '.join(valid_payment_methods)}"
            }), 400

        # Ensure emails is a valid array
        if electronic:
            if not isinstance(emails, list):
                emails = [emails] if isinstance(emails, str) else []

            if not emails:
                return jsonify({"status": "error", "message": "Emails must be a non-empty array for electronic receipts"}), 400

        # Validate items
        if not items:
            return jsonify({"status": "error", "message": "No items provided for the receipt"}), 400

        # Concatenate all items into a single string
        concatenated_items = ", ".join(
            f"{item['quantity']} x {item['name']}{' - LOT: ' + item['lotNumber'] if item.get('lotNumber') else ''}"
            for item in items
        )

        # Use the concatenated string as the single item's name
        total_price = sum(item['price'] * item['quantity'] for item in items)
        vat_rate = items[0].get('vat', '27%')  # Assuming all items have the same VAT rate

        # Prepare a single item for the API
        prepared_items = [
            {
                "name": concatenated_items,
                "unit_price": total_price,
                "vat": vat_rate,
                "quantity": 1,
            }
        ]

        # Construct the payload
        payload = {
            "partner_id": 0,  # Set your partner ID
            "block_id": BILLINGO_RECEIPT_BLOCK_ID,
            "type": "receipt",
            "payment_method": payment_method,
            "currency": "HUF",
            "conversion_rate": 1,
            "electronic": electronic,
            "items": prepared_items,
        }

        if electronic:
            payload["emails"] = emails
            payload["name"] = customer_name

        if discount > 0:
            payload["discount"] = discount

        # API headers
        headers = {
            "X-API-KEY": BILLINGO_API_KEY,
            "Content-Type": "application/json",
        }

        # Send the request to the Billingo API
        response = requests.post(f"{BILLINGO_BASE_URL}/documents/receipt", json=payload, headers=headers)

        if response.status_code == 201:
            last_receipt_data = response.json()
            return jsonify({"status": "success", "data": last_receipt_data}), 201
        else:
            return jsonify({"status": "error", "message": response.json()}), response.status_code

    except Exception as e:
        logging.error(f"Error creating receipt: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/download_pos_print', methods=['GET'])
@login_required
def download_pos_print():
    logging.debug("Current last_receipt_data: %s", last_receipt_data)

    if last_receipt_data and "id" in last_receipt_data:
        document_id = last_receipt_data["id"]
    else:
        return jsonify({"status": "error", "message": "Document ID is missing or last receipt data is empty."}), 404

    headers = {
        "X-API-KEY": BILLINGO_API_KEY,
    }

    response = requests.get(f"{BILLINGO_BASE_URL}/documents/{document_id}/print/pos", headers=headers)

    if response.status_code == 200:
        pdf_stream = io.BytesIO(response.content)
        return send_file(pdf_stream, as_attachment=True, download_name="pos_receipt.pdf", mimetype="application/pdf")
    elif response.status_code == 202:
        return jsonify({"status": "error", "message": "PDF generation in progress. Try again later."}), 202
    else:
        return jsonify({"status": "error", "message": response.json()}), response.status_code


@app.route('/cancel_document/<int:document_id>', methods=['POST'])
@login_required
def cancel_document(document_id):
    """Cancel/Sztornó a document via Billingo API and mark sale as cancelled"""
    try:
        data = request.get_json() or {}
        cancellation_reason = data.get('cancellation_reason', 'Sztornó')
        sale_id = data.get('sale_id')  # Optional: to mark in database

        headers = {
            "X-API-KEY": BILLINGO_API_KEY,
            "Content-Type": "application/json",
        }

        payload = {
            "cancellation_reason": cancellation_reason
        }

        # Call Billingo API to cancel the document
        response = requests.post(
            f"{BILLINGO_BASE_URL}/documents/{document_id}/cancel",
            json=payload,
            headers=headers
        )

        if response.status_code == 200:
            cancel_data = response.json()

            # Mark the sale as cancelled in the database
            # First try by sale_id, then by receipt_id
            if sale_id:
                query_db("UPDATE market_sales SET cancelled = 1 WHERE id = ?", [sale_id])
            else:
                query_db("UPDATE market_sales SET cancelled = 1 WHERE receipt_id = ?", [str(document_id)])

            return jsonify({
                "status": "success",
                "message": "Document cancelled successfully",
                "data": cancel_data
            }), 200
        else:
            error_msg = response.json() if response.content else {"message": "Unknown error"}
            return jsonify({
                "status": "error",
                "message": error_msg
            }), response.status_code

    except Exception as e:
        logging.error(f"Error cancelling document: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


# Market Session Routes

def get_active_market_session():
    """Get the currently active market session (not closed)"""
    return query_db("SELECT * FROM market_sessions WHERE closed_at IS NULL ORDER BY created_at DESC LIMIT 1", one=True)


def get_packaged_products():
    """Get packaged products from roast_tracker.db for market preparation"""
    from roast_tracker.database import get_db as get_roast_db

    conn = get_roast_db()
    cur = conn.cursor()

    # Query production batches with product info
    # Only get whole_bean packages (marketable sizes)
    cur.execute("""
        SELECT
            pb.id as production_batch_id,
            pb.production_lot,
            pb.production_type,
            pb.package_size_g,
            pb.quantity,
            pb.production_date,
            rb.lot_number as source_lot,
            rb.roast_level,
            cp.name as product_name,
            cp.image_url,
            gc.country
        FROM production_batches pb
        JOIN production_sources ps ON pb.id = ps.production_batch_id
        JOIN roast_batches rb ON ps.roast_batch_id = rb.id
        JOIN coffee_products cp ON rb.product_id = cp.id
        LEFT JOIN green_coffee gc ON cp.green_coffee_id = gc.id
        WHERE pb.production_type IN ('whole_bean_250', 'whole_bean_70', 'whole_bean_16', 'drip_11')
        ORDER BY pb.production_date DESC, cp.name
    """)

    results = cur.fetchall()
    conn.close()

    # Convert to list of dicts
    packages = []
    for row in results:
        packages.append({
            'id': row['production_batch_id'],
            'production_lot': row['production_lot'],
            'production_type': row['production_type'],
            'package_size_g': row['package_size_g'],
            'quantity': row['quantity'],
            'production_date': row['production_date'],
            'source_lot': row['source_lot'],
            'roast_level': row['roast_level'],
            'product_name': row['product_name'],
            'image_url': row['image_url'],
            'country': row['country']
        })

    return packages


@app.route('/prepare_market')
@login_required
def prepare_market():
    active_session = get_active_market_session()
    items = query_db("SELECT * FROM items")
    categories = query_db("SELECT * FROM categories")

    # Get packaged products from roast tracker
    packaged_products = get_packaged_products()

    session_items = []
    if active_session:
        session_items = query_db("""
            SELECT msi.id, msi.item_id, msi.lot_number, msi.quantity_prepared, msi.quantity_remaining,
                   i.name, i.price, i.image_url
            FROM market_session_items msi
            JOIN items i ON msi.item_id = i.id
            WHERE msi.session_id = ?
            ORDER BY i.name, msi.lot_number
        """, [active_session[0]])

    return render_template('prepare_market.html',
                          active_session=active_session,
                          items=items,
                          categories=categories,
                          session_items=session_items,
                          packaged_products=packaged_products,
                          billingo_env=BILLINGO_ENV)

@app.route('/create_market_session', methods=['POST'])
@login_required
def create_market_session():
    try:
        data = request.get_json()
        name = data.get('name', 'Market Session')
        initial_cash = float(data.get('initial_cash', 0))

        # Close any existing active session first
        query_db("UPDATE market_sessions SET closed_at = CURRENT_TIMESTAMP WHERE closed_at IS NULL")

        # Create new session with initial cash
        query_db("INSERT INTO market_sessions (name, initial_cash) VALUES (?, ?)", (name, initial_cash))

        return jsonify({"status": "success", "message": "Market session created"})
    except Exception as e:
        logging.error(f"Error creating market session: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/close_market_session', methods=['POST'])
@login_required
def close_market_session():
    try:
        # Get summary before closing
        active_session = get_active_market_session()
        if not active_session:
            return jsonify({"status": "error", "message": "No active session to close"}), 400

        session_id = active_session[0]
        summary = get_session_summary(session_id)

        query_db("UPDATE market_sessions SET closed_at = CURRENT_TIMESTAMP WHERE id = ?", [session_id])

        return jsonify({
            "status": "success",
            "message": "Market session closed",
            "summary": summary
        })
    except Exception as e:
        logging.error(f"Error closing market session: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/reopen_market_session/<int:session_id>', methods=['POST'])
@login_required
def reopen_market_session(session_id):
    """Reopen a closed market session"""
    try:
        # Check if there's already an active session
        active_session = get_active_market_session()
        if active_session:
            return jsonify({
                "status": "error",
                "message": "Cannot reopen: another session is already active. Close it first."
            }), 400

        # Check if the session exists and is closed
        session = query_db("SELECT id, closed_at FROM market_sessions WHERE id = ?", [session_id], one=True)
        if not session:
            return jsonify({"status": "error", "message": "Session not found"}), 404

        if not session[1]:  # closed_at is NULL, already active
            return jsonify({"status": "error", "message": "Session is already active"}), 400

        # Reopen the session by setting closed_at to NULL
        query_db("UPDATE market_sessions SET closed_at = NULL WHERE id = ?", [session_id])

        return jsonify({
            "status": "success",
            "message": "Market session reopened"
        })
    except Exception as e:
        logging.error(f"Error reopening market session: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/record_market_sale', methods=['POST'])
@login_required
def record_market_sale():
    """Record a sale in the market session"""
    try:
        data = request.get_json()
        total_amount = float(data.get('total_amount', 0))
        payment_method = data.get('payment_method', 'cash')
        items_json = data.get('items_json', '[]')
        receipt_id = data.get('receipt_id', '')

        # Get current user from session
        sold_by = session.get('username', 'Unknown')

        active_session = get_active_market_session()
        if not active_session:
            return jsonify({"status": "error", "message": "No active market session"}), 400

        session_id = active_session[0]

        query_db("""
            INSERT INTO market_sales (session_id, total_amount, payment_method, items_json, receipt_id, sold_by)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (session_id, total_amount, payment_method, items_json, receipt_id, sold_by))

        return jsonify({"status": "success", "message": "Sale recorded"})
    except Exception as e:
        logging.error(f"Error recording market sale: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

def get_session_summary(session_id):
    """Get summary statistics for a market session"""
    session = query_db("SELECT * FROM market_sessions WHERE id = ?", [session_id], one=True)
    if not session:
        return None

    # Get all sales for this session
    sales = query_db("SELECT * FROM market_sales WHERE session_id = ?", [session_id])

    # Calculate totals by payment method
    cash_total = sum(sale[3] for sale in sales if sale[4] == 'cash')
    card_total = sum(sale[3] for sale in sales if sale[4] == 'bankcard')
    other_total = sum(sale[3] for sale in sales if sale[4] not in ['cash', 'bankcard'])

    total_sales = cash_total + card_total + other_total
    initial_cash = session[4] if len(session) > 4 else 0  # initial_cash column

    # Get items sold summary
    items_prepared = query_db("""
        SELECT i.name, msi.lot_number, msi.quantity_prepared, msi.quantity_remaining,
               (msi.quantity_prepared - msi.quantity_remaining) as quantity_sold
        FROM market_session_items msi
        JOIN items i ON msi.item_id = i.id
        WHERE msi.session_id = ?
        ORDER BY i.name
    """, [session_id])

    return {
        "session_name": session[1],
        "created_at": session[2],
        "initial_cash": initial_cash,
        "cash_sales": cash_total,
        "card_sales": card_total,
        "other_sales": other_total,
        "total_sales": total_sales,
        "expected_cash": initial_cash + cash_total,
        "transaction_count": len(sales),
        "items_sold": [
            {
                "name": item[0],
                "lot_number": item[1],
                "prepared": item[2],
                "remaining": item[3],
                "sold": item[4]
            }
            for item in items_prepared if item[4] > 0
        ]
    }

@app.route('/get_session_summary')
@login_required
def get_session_summary_route():
    """API endpoint to get current session summary"""
    active_session = get_active_market_session()
    if not active_session:
        return jsonify({"status": "error", "message": "No active session"}), 400

    summary = get_session_summary(active_session[0])
    return jsonify({"status": "success", "summary": summary})

@app.route('/add_market_item', methods=['POST'])
@login_required
def add_market_item():
    try:
        data = request.get_json()
        lot_number = data.get('lot_number')
        quantity = int(data.get('quantity', 1))

        # Support both old (item_id) and new (production_batch_id + product_name) formats
        item_id = data.get('item_id')
        production_batch_id = data.get('production_batch_id')
        product_name = data.get('product_name')

        active_session = get_active_market_session()
        if not active_session:
            return jsonify({"status": "error", "message": "No active market session"}), 400

        session_id = active_session[0]

        # If using package system, find matching item by product name
        if production_batch_id and product_name and not item_id:
            # Try to find item by name (partial match)
            item = query_db("""
                SELECT id FROM items WHERE name LIKE ?
            """, [f"%{product_name}%"], one=True)

            if item:
                item_id = item[0]
            else:
                # Create a placeholder item for this product
                # First, find or create a "Roast Tracker" category
                roast_cat = query_db("SELECT id FROM categories WHERE name = 'Roast Tracker'", one=True)
                if not roast_cat:
                    query_db("INSERT INTO categories (name, is_coffee_shop) VALUES ('Roast Tracker', 0)")
                    roast_cat = query_db("SELECT id FROM categories WHERE name = 'Roast Tracker'", one=True)

                # Create the item
                query_db("""
                    INSERT INTO items (name, price, vat, category_id)
                    VALUES (?, 0, '27%', ?)
                """, (product_name, roast_cat[0]))
                item = query_db("SELECT id FROM items WHERE name = ?", [product_name], one=True)
                item_id = item[0]

        if not item_id:
            return jsonify({"status": "error", "message": "No item selected"}), 400

        # Check if this exact item+lot combination already exists
        existing = query_db("""
            SELECT id, quantity_prepared, quantity_remaining FROM market_session_items
            WHERE session_id = ? AND item_id = ? AND lot_number = ?
        """, [session_id, item_id, lot_number], one=True)

        if existing:
            # Update existing entry
            new_prepared = existing[1] + quantity
            new_remaining = existing[2] + quantity
            query_db("""
                UPDATE market_session_items
                SET quantity_prepared = ?, quantity_remaining = ?
                WHERE id = ?
            """, (new_prepared, new_remaining, existing[0]))
        else:
            # Insert new entry
            query_db("""
                INSERT INTO market_session_items (session_id, item_id, lot_number, quantity_prepared, quantity_remaining)
                VALUES (?, ?, ?, ?, ?)
            """, (session_id, item_id, lot_number, quantity, quantity))

        return jsonify({"status": "success", "message": "Item added to market session"})
    except Exception as e:
        logging.error(f"Error adding market item: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/remove_market_item/<int:id>', methods=['POST'])
@login_required
def remove_market_item(id):
    try:
        query_db("DELETE FROM market_session_items WHERE id = ?", [id])
        return jsonify({"status": "success", "message": "Item removed"})
    except Exception as e:
        logging.error(f"Error removing market item: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/update_market_item_quantity/<int:id>', methods=['POST'])
@login_required
def update_market_item_quantity(id):
    try:
        data = request.get_json()
        quantity = int(data.get('quantity', 0))

        if quantity <= 0:
            query_db("DELETE FROM market_session_items WHERE id = ?", [id])
        else:
            # Get current values
            item = query_db("SELECT quantity_prepared, quantity_remaining FROM market_session_items WHERE id = ?", [id], one=True)
            if item:
                diff = quantity - item[0]
                new_remaining = item[1] + diff
                if new_remaining < 0:
                    new_remaining = 0
                query_db("""
                    UPDATE market_session_items
                    SET quantity_prepared = ?, quantity_remaining = ?
                    WHERE id = ?
                """, (quantity, new_remaining, id))

        return jsonify({"status": "success", "message": "Quantity updated"})
    except Exception as e:
        logging.error(f"Error updating market item quantity: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/market_mode')
@login_required
def market_mode():
    active_session = get_active_market_session()

    if not active_session:
        return redirect(url_for('prepare_market'))

    # Get market session items with remaining quantity > 0, including category and attributes
    market_items = query_db("""
        SELECT msi.id, msi.item_id, msi.lot_number, msi.quantity_remaining,
               i.name, i.price, i.vat, i.image_url, c.name as category_name, c.id as category_id,
               i.attributes
        FROM market_session_items msi
        JOIN items i ON msi.item_id = i.id
        LEFT JOIN categories c ON i.category_id = c.id
        WHERE msi.session_id = ? AND msi.quantity_remaining > 0
        ORDER BY c.name, i.name, msi.lot_number
    """, [active_session[0]])

    # Get unique categories from market items for grouping
    market_categories = query_db("""
        SELECT DISTINCT c.id, c.name
        FROM market_session_items msi
        JOIN items i ON msi.item_id = i.id
        LEFT JOIN categories c ON i.category_id = c.id
        WHERE msi.session_id = ? AND msi.quantity_remaining > 0
        ORDER BY c.name
    """, [active_session[0]])

    # Extract unique origins and roasts from market items
    origins = set()
    roasts = set()
    for item in market_items:
        if item[10]:  # attributes column (index 10)
            try:
                attrs = json.loads(item[10])
                if attrs.get('Origin'):
                    origins.add(attrs['Origin'])
                if attrs.get('Roast'):
                    roasts.add(attrs['Roast'])
            except:
                pass

    # Get coffee shop items (from categories marked as coffee shop)
    coffee_items = query_db("""
        SELECT i.id, i.name, i.price, i.vat, i.image_url, c.name as category_name
        FROM items i
        JOIN categories c ON i.category_id = c.id
        WHERE c.is_coffee_shop = 1
        ORDER BY c.name, i.name
    """)

    # Get coffee shop categories for filtering
    coffee_categories = query_db("SELECT * FROM categories WHERE is_coffee_shop = 1")

    # Get 500g products from catalog (to enable virtual 500g option when 2x250g available)
    # These are products with "500g" in the name that can be fulfilled with 2x250g
    products_500g = query_db("""
        SELECT id, name, price, vat, image_url, attributes
        FROM items
        WHERE name LIKE '%500g%'
    """)

    return render_template('market_mode.html',
                          active_session=active_session,
                          market_items=market_items,
                          market_categories=market_categories,
                          coffee_items=coffee_items,
                          coffee_categories=coffee_categories,
                          origins=sorted(origins),
                          roasts=sorted(roasts),
                          products_500g=products_500g)

@app.route('/market_sale', methods=['POST'])
@login_required
def market_sale():
    """Process a sale and update market item quantities"""
    try:
        data = request.get_json()
        market_item_sales = data.get('market_item_sales', [])  # List of {market_session_item_id, quantity}

        for sale in market_item_sales:
            item_id = sale.get('market_session_item_id')
            quantity = sale.get('quantity', 1)

            # Decrease remaining quantity
            query_db("""
                UPDATE market_session_items
                SET quantity_remaining = quantity_remaining - ?
                WHERE id = ? AND quantity_remaining >= ?
            """, (quantity, item_id, quantity))

        return jsonify({"status": "success"})
    except Exception as e:
        logging.error(f"Error processing market sale: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/market_history')
@login_required
def market_history():
    """View all past market sessions"""
    sessions = query_db("""
        SELECT
            ms.id,
            ms.name,
            ms.created_at,
            ms.closed_at,
            ms.initial_cash,
            COALESCE(SUM(CASE WHEN msa.payment_method = 'cash' AND COALESCE(msa.cancelled, 0) = 0 THEN msa.total_amount ELSE 0 END), 0) as cash_sales,
            COALESCE(SUM(CASE WHEN msa.payment_method = 'bankcard' AND COALESCE(msa.cancelled, 0) = 0 THEN msa.total_amount ELSE 0 END), 0) as card_sales,
            COALESCE(SUM(CASE WHEN COALESCE(msa.cancelled, 0) = 0 THEN msa.total_amount ELSE 0 END), 0) as total_sales,
            COUNT(CASE WHEN COALESCE(msa.cancelled, 0) = 0 THEN msa.id END) as transaction_count
        FROM market_sessions ms
        LEFT JOIN market_sales msa ON ms.id = msa.session_id
        GROUP BY ms.id
        ORDER BY ms.created_at DESC
    """)
    return render_template('market_history.html', sessions=sessions)

@app.route('/market_session_detail/<int:session_id>')
@login_required
def market_session_detail(session_id):
    """View detailed information about a specific market session"""
    session = query_db("""
        SELECT id, name, created_at, closed_at, initial_cash
        FROM market_sessions WHERE id = ?
    """, [session_id], one=True)

    if not session:
        return redirect(url_for('market_history'))

    # Get sales for this session (including cancelled status)
    sales = query_db("""
        SELECT id, sale_time, total_amount, payment_method, items_json, receipt_id, sold_by, COALESCE(cancelled, 0) as cancelled
        FROM market_sales
        WHERE session_id = ?
        ORDER BY sale_time DESC
    """, [session_id])

    # Get stock tracking (prepared vs remaining)
    stock = query_db("""
        SELECT
            i.name,
            msi.lot_number,
            msi.quantity_prepared,
            msi.quantity_remaining,
            (msi.quantity_prepared - msi.quantity_remaining) as quantity_sold,
            i.price
        FROM market_session_items msi
        JOIN items i ON msi.item_id = i.id
        WHERE msi.session_id = ?
        ORDER BY i.name, msi.lot_number
    """, [session_id])

    # Calculate summary (exclude cancelled sales - index 7 is cancelled flag)
    active_sales = [s for s in sales if not s[7]]  # Filter out cancelled sales
    cash_total = sum(s[2] for s in active_sales if s[3] == 'cash')
    card_total = sum(s[2] for s in active_sales if s[3] == 'bankcard')
    other_total = sum(s[2] for s in active_sales if s[3] not in ('cash', 'bankcard'))
    total_sales = cash_total + card_total + other_total
    initial_cash = session[4] if session[4] else 0

    # Calculate coffee shop sales from items_json (exclude cancelled sales)
    # Coffee shop items don't have LOT numbers - that's the key identifier
    coffee_sales = {}  # name -> {quantity, revenue}

    for sale in active_sales:  # Only process non-cancelled sales
        items_json = sale[4]
        if items_json:
            try:
                items = json.loads(items_json)
                for item in items:
                    name = item.get('name', 'Unknown')
                    qty = item.get('quantity', 1)
                    price = item.get('price', 0)
                    lot_number = item.get('lotNumber', '')
                    item_id = item.get('itemId', item.get('id', 0))

                    # Coffee shop items: no LOT number
                    # This is the simplest and most reliable way to identify them
                    if not lot_number:
                        if name not in coffee_sales:
                            coffee_sales[name] = {'quantity': 0, 'revenue': 0}
                        coffee_sales[name]['quantity'] += qty
                        coffee_sales[name]['revenue'] += qty * price
            except (json.JSONDecodeError, TypeError):
                pass

    # Convert to list for template
    coffee_sales_list = [
        {'name': name, 'quantity': data['quantity'], 'revenue': data['revenue']}
        for name, data in sorted(coffee_sales.items())
    ]
    coffee_sales_total = sum(item['revenue'] for item in coffee_sales_list)

    # Count cancelled sales for display
    cancelled_count = len([s for s in sales if s[7]])

    summary = {
        "initial_cash": initial_cash,
        "cash_sales": cash_total,
        "card_sales": card_total,
        "other_sales": other_total,
        "total_sales": total_sales,
        "expected_cash": initial_cash + cash_total,
        "transaction_count": len(active_sales),
        "cancelled_count": cancelled_count,
        "coffee_sales_total": coffee_sales_total
    }

    return render_template('market_session_detail.html',
                          session=session,
                          sales=sales,
                          stock=stock,
                          summary=summary,
                          coffee_sales=coffee_sales_list)

# ==================== Customer Display ====================

@app.route('/customer_display')
@login_required
def customer_display():
    """Customer-facing display showing current sale for logged-in user"""
    username = session.get('username')
    return render_template('customer_display.html', username=username)

@app.route('/api/update_sale', methods=['POST'])
@login_required
def update_sale():
    """Update the current sale for a user (called from market_mode)"""
    try:
        username = session.get('username')
        if not username:
            return jsonify({"status": "error", "message": "Not logged in"}), 401

        data = request.get_json()
        items = data.get('items', [])
        total = data.get('total', 0)
        discount = data.get('discount', 0)
        status = data.get('status', 'active')  # 'active', 'completed', 'cleared'

        active_sales[username] = {
            'items': items,
            'total': total,
            'discount': discount,
            'status': status,
            'updated_at': time.time()
        }

        return jsonify({"status": "success"})
    except Exception as e:
        logging.error(f"Error updating sale: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/get_sale')
@login_required
def get_sale():
    """Get the current sale for a user (polled by customer display)"""
    username = session.get('username')
    if not username:
        return jsonify({"status": "error", "message": "Not logged in"}), 401

    sale_data = active_sales.get(username, {
        'items': [],
        'total': 0,
        'discount': 0,
        'status': 'empty',
        'updated_at': 0
    })

    return jsonify({
        "status": "success",
        "data": sale_data
    })

@app.route('/api/sale_stream')
@login_required
def sale_stream():
    """Server-Sent Events stream for real-time sale updates"""
    username = session.get('username')
    if not username:
        return jsonify({"status": "error", "message": "Not logged in"}), 401

    def generate():
        last_update = 0
        while True:
            sale_data = active_sales.get(username, {
                'items': [],
                'total': 0,
                'discount': 0,
                'status': 'empty',
                'updated_at': 0
            })

            # Only send if there's a new update
            if sale_data.get('updated_at', 0) > last_update:
                last_update = sale_data.get('updated_at', 0)
                yield f"data: {json.dumps(sale_data)}\n\n"

            time.sleep(0.5)  # Check every 500ms

    return Response(generate(), mimetype='text/event-stream',
                   headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})

if __name__ == '__main__':
    # debug=True for local development, set to False for production/remote access
    debug_mode = os.environ.get("FLASK_DEBUG", "false").lower() == "true"
    app.run(host='0.0.0.0', port=5000, debug=debug_mode)
