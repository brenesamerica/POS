from flask import Flask, request, jsonify, render_template, redirect, url_for, send_file
import sqlite3
import requests
import logging
import io
import xml.etree.ElementTree as ET

app = Flask(__name__)
DATABASE = 'pos.db'

# Set up logging for debugging
logging.basicConfig(level=logging.DEBUG)

# Billingo API settings
BILLINGO_API_KEY = "dc7626a6-9ed2-11ef-9815-0254eb6072a0"
BILLINGO_BASE_URL = "https://api.billingo.hu/v3"

# Database helper functions
def query_db(query, args=(), one=False):
    conn = sqlite3.connect(DATABASE)
    cur = conn.cursor()
    cur.execute(query, args)
    rv = cur.fetchall()
    conn.commit()
    conn.close()
    return (rv[0] if rv else None) if one else rv

import sqlite3

def init_db():
    with sqlite3.connect('pos.db') as conn:
        cur = conn.cursor()
        # Create categories table
        cur.execute("""
            CREATE TABLE IF NOT EXISTS categories (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL
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
                FOREIGN KEY (category_id) REFERENCES categories (id)
            )
        """)
        conn.commit()

API_KEY = 'XE3XXEMIECYR7C7ERXHAJ8NN5P5RX42M'
BASE_URL = 'https://cafetiko.com/api/'

def fetch_products():
    url = f"{BASE_URL}products?ws_key={API_KEY}"
    response = requests.get(url)
    products = []

    if response.status_code == 200:
        root = ET.fromstring(response.content)
        for product in root.findall('.//product'):
            product_id = product.get('id')
            product_details = fetch_product_details(product_id)
            if product_details:
                products.append(product_details)
                logging.debug(f"Fetched product: {product_details}")
            else:
                logging.error(f"Failed to fetch details for product ID: {product_id}")
    else:
        logging.error("Failed to fetch products list from API")
    
    return products

def fetch_product_details(product_id):
    url = f"{BASE_URL}products/{product_id}?ws_key={API_KEY}"
    response = requests.get(url)

    if response.status_code == 200:
        root = ET.fromstring(response.content)
        
        # Check if product is active
        active_status = root.find('.//active').text
        if active_status != '1':
            logging.debug(f"Product ID {product_id} is not active. Skipping.")
            return None

        # Extract Hungarian name (language id="2")
        name = root.find('.//name/language[@id="2"]').text

        # Apply VAT multiplier to the price
        price = float(root.find('.//price').text) * 1.27

        # Extract other product details
        category_id = int(root.find('.//id_category_default').text)
        description = root.find('.//description/language[@id="2"]').text
        image_id = root.find('.//id_default_image').text
        image_url = f"{BASE_URL}images/products/{product_id}/{image_id}?ws_key={API_KEY}"

        # Validate data
        if not name or not price or not category_id:
            logging.error(f"Missing essential field for product {product_id}. Skipping.")
            return None

        return {
            'id': product_id,
            'name': name,
            'price': price,
            'category_id': category_id,
            'description': description,
            'image_url': image_url
        }
    else:
        logging.error(f"Failed to fetch product details for ID: {product_id}")
    return None

def fetch_categories_with_products():
    url = f"{BASE_URL}categories?ws_key={API_KEY}"
    response = requests.get(url)
    categories = []

    if response.status_code == 200:
        root = ET.fromstring(response.content)
        for category in root.findall('.//category'):
            category_id = category.get('id')
            product_url = f"{BASE_URL}products?filter[id_category_default]={category_id}&ws_key={API_KEY}"
            product_response = requests.get(product_url)

            # Only add categories with associated products
            if product_response.status_code == 200:
                product_root = ET.fromstring(product_response.content)
                products = product_root.findall('.//product')
                if products:  # Only add category if it has products
                    category_name = fetch_category_name(category_id)
                    categories.append({'id': category_id, 'name': category_name})
    return categories


@app.route('/update_catalog', methods=['POST'])
def update_catalog_route():
    try:
        update_catalog()
        return jsonify({"status": "success", "message": "Catalog updated successfully"})
    except Exception as e:
        logging.error("Error updating catalog: %s", e)
        return jsonify({"status": "error", "message": str(e)}), 500

def update_catalog():
    products = fetch_products()
    categories = fetch_categories_with_products()

    with sqlite3.connect('pos.db') as conn:
        cursor = conn.cursor()

        # Clear old data
        cursor.execute('DELETE FROM categories')
        cursor.execute('DELETE FROM items')

        # Insert categories with products only
        for category in categories:
            cursor.execute("INSERT INTO categories (id, name) VALUES (?, ?)", (category['id'], category['name']))
            logging.debug(f"Inserted category: {category}")

        # Insert products
        for product in products:
            if product:  # Only insert if product details are valid
                cursor.execute("""
                    INSERT INTO items (id, name, price, vat, category_id, image_url, description) 
                    VALUES (?, ?, ?, '27%', ?, ?, ?)
                """, (product['id'], product['name'], product['price'], product['category_id'], product['image_url'], product['description']))
                logging.debug(f"Inserted item: {product}")

        conn.commit()


@app.route('/verify_data', methods=['GET'])
def verify_data():
    categories = query_db("SELECT * FROM categories")
    items = query_db("SELECT * FROM items")
    logging.debug(f"Categories in DB: {categories}")
    logging.debug(f"Items in DB: {items}")
    return jsonify({"categories": categories, "items": items})

def fetch_category_name(category_id):
    url = f"{BASE_URL}categories/{category_id}?ws_key={API_KEY}"
    response = requests.get(url)

    if response.status_code == 200:
        root = ET.fromstring(response.content)
        name = root.find('.//name/language').text
        return name
    return ""

# Initialize the database
init_db()
# Main menu
@app.route('/')
def main_menu():
    return render_template('main_menu.html')

# Manage categories page
@app.route('/manage_categories')
def manage_categories():
    categories = query_db("SELECT * FROM categories")
    return render_template('manage_categories.html', categories=categories)

# Add category page
@app.route('/add_category', methods=['GET', 'POST'])
def add_category():
    if request.method == 'POST':
        name = request.form.get('name')
        query_db("INSERT INTO categories (name) VALUES (?)", (name,))
        return redirect(url_for('manage_categories'))
    return render_template('add_category.html')

# Edit category page
@app.route('/edit_category/<int:id>', methods=['GET', 'POST'])
def edit_category(id):
    category = query_db("SELECT * FROM categories WHERE id = ?", [id], one=True)
    if request.method == 'POST':
        name = request.form.get('name')
        query_db("UPDATE categories SET name = ? WHERE id = ?", (name, id))
        return redirect(url_for('manage_categories'))
    return render_template('edit_category.html', category=category)

# Delete category endpoint
@app.route('/delete_category/<int:id>', methods=['POST'])
def delete_category(id):
    query_db("DELETE FROM categories WHERE id = ?", [id])
    return jsonify({"status": "success", "message": "Category deleted successfully"})

# Manage items page
@app.route('/manage_items')
def manage_items():
    items = query_db("SELECT * FROM items")
    categories = query_db("SELECT * FROM categories")
    return render_template('manage_items.html', items=items, categories=categories)

# Add item page
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

# Edit item page
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

# Delete item endpoint
@app.route('/delete_item/<int:id>', methods=['POST'])
def delete_item(id):
    query_db("DELETE FROM items WHERE id = ?", [id])
    return jsonify({"status": "success", "message": "Item deleted successfully"})

# Create receipt page
@app.route('/create_receipt')
def create_receipt_page():
    items = query_db("SELECT * FROM items")
    categories = query_db("SELECT * FROM categories")
    return render_template('create_receipt.html', items=items, categories=categories)


@app.route('/create_receipt', methods=['POST'])
def create_receipt():
    global last_receipt_data
    data = request.get_json()
    items = data.get('items', [])
    discount = data.get('discount', 0)
    payment_method = data.get('payment_method')
    electronic = data.get('electronic', False)
    name = data.get('name', "")
    emails = data.get('emails', [])

    # Ensure email is provided for electronic receipts
    if electronic and not emails:
        return jsonify({"status": "error", "message": "Email is required for electronic receipts"}), 400

    # Check for missing 'price' or 'quantity' fields
    for item in items:
        if 'price' not in item or 'quantity' not in item or 'name' not in item:
            logging.error("Item is missing 'price' or 'quantity': %s", item)
            return jsonify({"status": "error", "message": "Each item must have 'price' and 'quantity'"}), 400

    # Prepare single concatenated item entry for Billingo
    total_price = items[0]['price']  # Combined price of all items
    concatenated_item_name = items[0]['name']  # Combined description of all items

    prepared_items = [
        {
            "name": concatenated_item_name,
            "unit_price": total_price,
            "vat": "27%",
            "quantity": 1
        }
    ]

    # Build the payload
    payload = {
        "partner_id": 99292,
        "block_id": 262126,
        "type": "receipt",
        "payment_method": payment_method,
        "currency": "HUF",
        "conversion_rate": 1,
        "electronic": electronic,
        "items": prepared_items,
    }

    # Only add the emails and name if electronic is true
    if electronic:
        payload["emails"] = emails
        payload["name"] = name

    headers = {
        "X-API-KEY": BILLINGO_API_KEY,
        "Content-Type": "application/json"
    }

    # Send request to Billingo API
    response = requests.post(f"{BILLINGO_BASE_URL}/documents/receipt", json=payload, headers=headers)
    last_receipt_data = response.json() if response.status_code == 201 else None  # Update last_receipt_data

    # Log the response to help debug
    logging.debug("Billingo API response for create_receipt: %s", last_receipt_data)

    if response.status_code == 201:
        return jsonify({"status": "success", "data": last_receipt_data}), 201
    else:
        return jsonify({"status": "error", "message": response.json()}), response.status_code


# Download POS print endpoint
@app.route('/download_pos_print', methods=['GET'])
def download_pos_print():
    # Log last_receipt_data for troubleshooting
    logging.debug("Current last_receipt_data: %s", last_receipt_data)

    # Check if last_receipt_data is not empty and contains a document ID
    if last_receipt_data and "id" in last_receipt_data:
        document_id = last_receipt_data["id"]
    else:
        return jsonify({"status": "error", "message": "Document ID is missing or last receipt data is empty."}), 404

    headers = {
        "X-API-KEY": BILLINGO_API_KEY,
    }

    # Request the POS print from Billingo API
    response = requests.get(f"{BILLINGO_BASE_URL}/documents/{document_id}/print/pos", headers=headers)

    # Handle different response cases
    if response.status_code == 200:
        # Success - Return the PDF as a downloadable file
        pdf_stream = io.BytesIO(response.content)
        return send_file(pdf_stream, as_attachment=True, download_name="pos_receipt.pdf", mimetype="application/pdf")
    elif response.status_code == 202:
        # PDF generation is still in progress
        return jsonify({"status": "error", "message": "PDF generation in progress. Try again later."}), 202
    else:
        # Error from the API
        return jsonify({"status": "error", "message": response.json()}), response.status_code

if __name__ == '__main__':
    app.run(debug=True)
