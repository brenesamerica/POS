from flask import Flask, request, jsonify, render_template, redirect, url_for, send_file, session
import sqlite3
import requests
import logging
import io
import os
import json


app = Flask(__name__)
DATABASE = 'pos.db'

app.secret_key = os.environ.get("FLASK_SECRET_KEY")
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

logging.info(f"Billingo environment: {BILLINGO_ENV}, Receipt Block: {BILLINGO_RECEIPT_BLOCK_ID}, Invoice Block: {BILLINGO_INVOICE_BLOCK_ID}")

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
    with sqlite3.connect('pos.db') as conn:
        cur = conn.cursor()
        # Create categories table
        cur.execute("""
            CREATE TABLE IF NOT EXISTS categories (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                is_coffee_shop INTEGER DEFAULT 0
            )
        """)
        # Create items table with new columns
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
                FOREIGN KEY (category_id) REFERENCES categories (id)
            )
        """)
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
        conn.commit()

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

@app.route('/google_login', methods=['POST'])
def google_login():
    try:
        # Get the JSON data sent from the frontend
        data = request.get_json()

        # Extract user information
        user_id = data.get('id')
        name = data.get('name')
        email = data.get('email')
        image_url = data.get('image_url')

        # Log the received data
        logging.info(f"Google user logged in: {name} ({email})")

        # Save user data in a session
        session['user_id'] = user_id
        session['name'] = name
        session['email'] = email
        session['image_url'] = image_url

        # Return a success response
        return jsonify({
            "status": "success",
            "message": "User logged in successfully",
            "data": {
                "id": user_id,
                "name": name,
                "email": email,
                "image_url": image_url,
            }
        }), 200

    except Exception as e:
        logging.error(f"Error handling Google login: {e}")
        return jsonify({
            "status": "error",
            "message": "An error occurred during Google login",
            "details": str(e)
        }), 500

@app.route('/logout', methods=['POST'])
def logout():
    try:
        # Clear the session
        session.clear()
        logging.info("User logged out successfully")
        return jsonify({"status": "success", "message": "Logged out successfully"}), 200
    except Exception as e:
        logging.error(f"Error logging out: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/update_catalog', methods=['POST'])
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

    with sqlite3.connect('pos.db') as conn:
        cursor = conn.cursor()

        # Clear old data
        cursor.execute('DELETE FROM categories')
        cursor.execute('DELETE FROM items')

        # Get the set of category IDs that have active products
        active_category_ids = set()
        for product in products:
            if product and product.get('category_id'):
                active_category_ids.add(product['category_id'])

        # Insert only categories that have active products
        categories_inserted = 0
        for category in categories:
            if category['id'] in active_category_ids:
                cursor.execute("INSERT INTO categories (id, name) VALUES (?, ?)", (category['id'], category['name']))
                categories_inserted += 1
                logging.debug(f"Inserted category: {category}")
            else:
                logging.debug(f"Skipped category (no active products): {category}")

        # Insert products
        products_inserted = 0
        products_with_attrs = 0
        for product in products:
            if product:  # Only insert if product details are valid
                # Serialize attributes to JSON
                attrs_json = json.dumps(product.get('attributes', {})) if product.get('attributes') else None
                if product.get('attributes'):
                    products_with_attrs += 1

                cursor.execute("""
                    INSERT INTO items (id, name, price, vat, category_id, image_url, description, attributes)
                    VALUES (?, ?, ?, '27%', ?, ?, ?, ?)
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
def verify_data():
    categories = query_db("SELECT * FROM categories")
    items = query_db("SELECT * FROM items")
    logging.debug(f"Categories in DB: {categories}")
    logging.debug(f"Items in DB: {items}")
    return jsonify({"categories": categories, "items": items})

# Initialize the database
init_db()

# Inject billingo_env into all templates automatically
@app.context_processor
def inject_billingo_env():
    return dict(billingo_env=BILLINGO_ENV)

@app.route('/')
def main_menu():
    return render_template('main_menu.html')

@app.route('/manage_categories')
def manage_categories():
    categories = query_db("SELECT * FROM categories")
    return render_template('manage_categories.html', categories=categories)

@app.route('/add_category', methods=['GET', 'POST'])
def add_category():
    if request.method == 'POST':
        name = request.form.get('name')
        is_coffee_shop = 1 if request.form.get('is_coffee_shop') else 0
        query_db("INSERT INTO categories (name, is_coffee_shop) VALUES (?, ?)", (name, is_coffee_shop))
        return redirect(url_for('manage_categories'))
    return render_template('add_category.html')

@app.route('/edit_category/<int:id>', methods=['GET', 'POST'])
def edit_category(id):
    category = query_db("SELECT * FROM categories WHERE id = ?", [id], one=True)
    if request.method == 'POST':
        name = request.form.get('name')
        is_coffee_shop = 1 if request.form.get('is_coffee_shop') else 0
        query_db("UPDATE categories SET name = ?, is_coffee_shop = ? WHERE id = ?", (name, is_coffee_shop, id))
        return redirect(url_for('manage_categories'))
    return render_template('edit_category.html', category=category)

@app.route('/delete_category/<int:id>', methods=['POST'])
def delete_category(id):
    query_db("DELETE FROM categories WHERE id = ?", [id])
    return jsonify({"status": "success", "message": "Category deleted successfully"})

@app.route('/manage_items')
def manage_items():
    items = query_db("""
        SELECT items.id, items.name, items.price, items.vat, categories.name AS category_name
        FROM items
        LEFT JOIN categories ON items.category_id = categories.id
    """)
    categories = query_db("SELECT * FROM categories ORDER BY name")
    return render_template('manage_items.html', items=items, categories=categories)


@app.route('/add_item', methods=['GET', 'POST'])
def add_item():
    categories = query_db("SELECT * FROM categories")
    if request.method == 'POST':
        name = request.form.get('name')
        price = float(request.form.get('price'))
        category_id = request.form.get('category_id')
        vat = request.form.get('vat', '27%')
        query_db("INSERT INTO items (name, price, vat, category_id) VALUES (?, ?, ?, ?)", (name, price, vat, category_id))
        return redirect(url_for('manage_items'))
    return render_template('add_item.html', categories=categories)

@app.route('/edit_item/<int:id>', methods=['GET', 'POST'])
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
def delete_item(id):
    query_db("DELETE FROM items WHERE id = ?", [id])
    return jsonify({"status": "success", "message": "Item deleted successfully"})

@app.route('/create_receipt')
def create_receipt_page():
    items = query_db("SELECT * FROM items")
    categories = query_db("SELECT * FROM categories")
    return render_template('create_receipt.html', items=items, categories=categories)



@app.route('/create_receipt', methods=['POST'])
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

# Market Session Routes

def get_active_market_session():
    """Get the currently active market session (not closed)"""
    return query_db("SELECT * FROM market_sessions WHERE closed_at IS NULL ORDER BY created_at DESC LIMIT 1", one=True)

@app.route('/prepare_market')
def prepare_market():
    active_session = get_active_market_session()
    items = query_db("SELECT * FROM items")
    categories = query_db("SELECT * FROM categories")

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
                          session_items=session_items)

@app.route('/create_market_session', methods=['POST'])
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

@app.route('/record_market_sale', methods=['POST'])
def record_market_sale():
    """Record a sale in the market session"""
    try:
        data = request.get_json()
        total_amount = float(data.get('total_amount', 0))
        payment_method = data.get('payment_method', 'cash')
        items_json = data.get('items_json', '[]')
        receipt_id = data.get('receipt_id', '')

        active_session = get_active_market_session()
        if not active_session:
            return jsonify({"status": "error", "message": "No active market session"}), 400

        session_id = active_session[0]

        query_db("""
            INSERT INTO market_sales (session_id, total_amount, payment_method, items_json, receipt_id)
            VALUES (?, ?, ?, ?, ?)
        """, (session_id, total_amount, payment_method, items_json, receipt_id))

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
def get_session_summary_route():
    """API endpoint to get current session summary"""
    active_session = get_active_market_session()
    if not active_session:
        return jsonify({"status": "error", "message": "No active session"}), 400

    summary = get_session_summary(active_session[0])
    return jsonify({"status": "success", "summary": summary})

@app.route('/add_market_item', methods=['POST'])
def add_market_item():
    try:
        data = request.get_json()
        item_id = data.get('item_id')
        lot_number = data.get('lot_number')
        quantity = int(data.get('quantity', 1))

        active_session = get_active_market_session()
        if not active_session:
            return jsonify({"status": "error", "message": "No active market session"}), 400

        session_id = active_session[0]

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
def remove_market_item(id):
    try:
        query_db("DELETE FROM market_session_items WHERE id = ?", [id])
        return jsonify({"status": "success", "message": "Item removed"})
    except Exception as e:
        logging.error(f"Error removing market item: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/update_market_item_quantity/<int:id>', methods=['POST'])
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
def market_mode():
    active_session = get_active_market_session()

    if not active_session:
        return redirect(url_for('prepare_market'))

    # Get market session items with remaining quantity > 0, including category
    market_items = query_db("""
        SELECT msi.id, msi.item_id, msi.lot_number, msi.quantity_remaining,
               i.name, i.price, i.vat, i.image_url, c.name as category_name, c.id as category_id
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

    return render_template('market_mode.html',
                          active_session=active_session,
                          market_items=market_items,
                          market_categories=market_categories,
                          coffee_items=coffee_items,
                          coffee_categories=coffee_categories)

@app.route('/market_sale', methods=['POST'])
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
def market_history():
    """View all past market sessions"""
    sessions = query_db("""
        SELECT
            ms.id,
            ms.name,
            ms.created_at,
            ms.closed_at,
            ms.initial_cash,
            COALESCE(SUM(CASE WHEN msa.payment_method = 'cash' THEN msa.total_amount ELSE 0 END), 0) as cash_sales,
            COALESCE(SUM(CASE WHEN msa.payment_method = 'bankcard' THEN msa.total_amount ELSE 0 END), 0) as card_sales,
            COALESCE(SUM(msa.total_amount), 0) as total_sales,
            COUNT(msa.id) as transaction_count
        FROM market_sessions ms
        LEFT JOIN market_sales msa ON ms.id = msa.session_id
        GROUP BY ms.id
        ORDER BY ms.created_at DESC
    """)
    return render_template('market_history.html', sessions=sessions)

@app.route('/market_session_detail/<int:session_id>')
def market_session_detail(session_id):
    """View detailed information about a specific market session"""
    session = query_db("""
        SELECT id, name, created_at, closed_at, initial_cash
        FROM market_sessions WHERE id = ?
    """, [session_id], one=True)

    if not session:
        return redirect(url_for('market_history'))

    # Get sales for this session
    sales = query_db("""
        SELECT id, sale_time, total_amount, payment_method, items_json, receipt_id
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

    # Calculate summary
    cash_total = sum(s[2] for s in sales if s[3] == 'cash')
    card_total = sum(s[2] for s in sales if s[3] == 'bankcard')
    other_total = sum(s[2] for s in sales if s[3] not in ('cash', 'bankcard'))
    total_sales = cash_total + card_total + other_total
    initial_cash = session[4] if session[4] else 0

    summary = {
        "initial_cash": initial_cash,
        "cash_sales": cash_total,
        "card_sales": card_total,
        "other_sales": other_total,
        "total_sales": total_sales,
        "expected_cash": initial_cash + cash_total,
        "transaction_count": len(sales)
    }

    return render_template('market_session_detail.html',
                          session=session,
                          sales=sales,
                          stock=stock,
                          summary=summary)

if __name__ == '__main__':
    # debug=True for local development, set to False for production/remote access
    debug_mode = os.environ.get("FLASK_DEBUG", "false").lower() == "true"
    app.run(host='0.0.0.0', port=5000, debug=debug_mode)
