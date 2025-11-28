"""
Flask routes for Roast Tracker
"""
from flask import Blueprint, render_template, request, jsonify, redirect, url_for, flash
from datetime import datetime, date
from functools import wraps
from .database import get_db, query_db, init_db
from .lot_generator import (
    generate_roast_lot, generate_drip_lot, generate_advent_lot,
    generate_cold_brew_lot, parse_lot_number, ROAST_LEVELS, MONTH_CODES
)
from .roasttime_import import (
    load_all_roasts, get_roast_by_uid, get_roast_summary,
    guess_roast_level, get_roasttime_path
)

roast_tracker = Blueprint('roast_tracker', __name__,
                          template_folder='../templates/roast_tracker',
                          url_prefix='/roast')


# Simple login check (reuse from main app)
def tracker_login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        from flask import session
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function


@roast_tracker.route('/')
@tracker_login_required
def dashboard():
    """Main dashboard showing available roasted coffee"""
    # Get roast batches with available stock
    batches = query_db("""
        SELECT rb.*, cp.name as product_name, gc.country
        FROM roast_batches rb
        JOIN coffee_products cp ON rb.product_id = cp.id
        LEFT JOIN green_coffee gc ON cp.green_coffee_id = gc.id
        WHERE rb.available_weight_g > 0
        ORDER BY rb.roast_date DESC
    """)

    # Get low stock alerts by PRODUCT (less than 300g total, including 0g)
    LOW_STOCK_THRESHOLD = 300
    low_stock_products = query_db("""
        SELECT cp.id, cp.name as product_name, cp.roast_level,
               gc.country,
               COALESCE(SUM(rb.available_weight_g), 0) as total_available_g
        FROM coffee_products cp
        LEFT JOIN green_coffee gc ON cp.green_coffee_id = gc.id
        LEFT JOIN roast_batches rb ON cp.id = rb.product_id
        WHERE cp.is_active = 1
        GROUP BY cp.id
        HAVING COALESCE(SUM(rb.available_weight_g), 0) < ?
        ORDER BY COALESCE(SUM(rb.available_weight_g), 0) ASC
    """, (LOW_STOCK_THRESHOLD,))

    # Get recent production
    recent_production = query_db("""
        SELECT pb.*, GROUP_CONCAT(rb.lot_number) as source_lots
        FROM production_batches pb
        LEFT JOIN production_sources ps ON pb.id = ps.production_batch_id
        LEFT JOIN roast_batches rb ON ps.roast_batch_id = rb.id
        GROUP BY pb.id
        ORDER BY pb.production_date DESC
        LIMIT 10
    """)

    # Summary stats
    total_available = sum(b['available_weight_g'] for b in batches) if batches else 0

    return render_template('roast_tracker/dashboard.html',
                           batches=batches,
                           low_stock=low_stock_products,
                           recent_production=recent_production,
                           total_available_kg=total_available / 1000,
                           roast_levels=ROAST_LEVELS)


@roast_tracker.route('/roast/new', methods=['GET', 'POST'])
@tracker_login_required
def new_roast():
    """Enter a new roast batch"""
    if request.method == 'POST':
        product_id = request.form.get('product_id', type=int)
        roast_level = request.form.get('roast_level')
        roast_date_str = request.form.get('roast_date')
        green_weight = request.form.get('green_weight_g', type=float)
        roasted_weight = request.form.get('roasted_weight_g', type=float)
        roasttime_uid = request.form.get('roasttime_uid', '')
        notes = request.form.get('notes', '')
        custom_sequence = request.form.get('custom_sequence', type=int)

        # Parse date
        roast_date = datetime.strptime(roast_date_str, '%Y-%m-%d').date()

        # Generate LOT number (with custom sequence if provided)
        lot_number = generate_roast_lot(roast_level, roast_date, product_id, custom_sequence)

        # Check if LOT already exists (same product, same day, same level)
        existing = query_db(
            "SELECT id FROM roast_batches WHERE lot_number = ?",
            (lot_number,), one=True
        )

        if existing:
            # Add to existing batch
            query_db("""
                UPDATE roast_batches
                SET green_weight_g = green_weight_g + ?,
                    roasted_weight_g = roasted_weight_g + ?,
                    available_weight_g = available_weight_g + ?
                WHERE lot_number = ?
            """, (green_weight, roasted_weight, roasted_weight, lot_number))
            flash(f'Added to existing batch {lot_number}', 'success')
        else:
            # Calculate weight loss
            weight_loss = ((green_weight - roasted_weight) / green_weight * 100) if green_weight > 0 else 0

            # Get RoastTime data if UID provided
            fc_time = None
            fc_temp = None
            drop_temp = None
            total_time = None
            preheat = None
            charge_temp = None
            ambient = None
            humidity = None

            if roasttime_uid:
                rt_data = get_roast_by_uid(roasttime_uid)
                if rt_data:
                    fc_time = rt_data.get('first_crack_time')
                    fc_temp = rt_data.get('first_crack_temp')
                    drop_temp = rt_data.get('drop_temp')
                    total_time = rt_data.get('total_roast_time')
                    preheat = rt_data.get('preheat_temp')
                    charge_temp = rt_data.get('charge_temp')
                    ambient = rt_data.get('ambient_temp')
                    humidity = rt_data.get('humidity')

            # Get day sequence
            day_seq = int(lot_number.split('/')[-1])

            # Insert new batch
            query_db("""
                INSERT INTO roast_batches (
                    lot_number, product_id, roast_date, roast_level, day_sequence,
                    green_weight_g, roasted_weight_g, available_weight_g, weight_loss_percent,
                    roasttime_uid, preheat_temp, charge_temp, first_crack_time, first_crack_temp,
                    drop_temp, total_roast_time, ambient_temp, humidity, notes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                lot_number, product_id, roast_date.isoformat(), roast_level, day_seq,
                green_weight, roasted_weight, roasted_weight, weight_loss,
                roasttime_uid, preheat, charge_temp, fc_time, fc_temp,
                drop_temp, total_time, ambient, humidity, notes
            ))
            flash(f'Created new batch: {lot_number}', 'success')

        return redirect(url_for('roast_tracker.dashboard'))

    # GET: Show form
    products = query_db("""
        SELECT cp.*, gc.country, gc.name as green_name
        FROM coffee_products cp
        LEFT JOIN green_coffee gc ON cp.green_coffee_id = gc.id
        WHERE cp.is_active = 1
        ORDER BY gc.country, cp.name
    """)

    # Get unique countries for filter
    countries = query_db("""
        SELECT DISTINCT gc.country
        FROM coffee_products cp
        LEFT JOIN green_coffee gc ON cp.green_coffee_id = gc.id
        WHERE cp.is_active = 1 AND gc.country IS NOT NULL
        ORDER BY gc.country
    """)
    country_list = [c['country'] for c in countries if c['country']]

    # Get recent RoastTime imports for selection
    roasttime_roasts = load_all_roasts()[:50]  # Last 50 roasts

    return render_template('roast_tracker/roast_entry.html',
                           products=products,
                           countries=country_list,
                           roast_levels=ROAST_LEVELS,
                           roasttime_roasts=roasttime_roasts,
                           today=date.today().isoformat())


@roast_tracker.route('/production', methods=['GET', 'POST'])
@tracker_login_required
def production():
    """Production/packaging screen"""
    if request.method == 'POST':
        production_type = request.form.get('production_type')
        roast_batch_id = request.form.get('roast_batch_id', type=int)
        quantity = request.form.get('quantity', type=int, default=1)
        custom_weight = request.form.get('custom_weight_g', type=float)
        notes = request.form.get('notes', '')

        # Get source batch
        batch = query_db(
            "SELECT * FROM roast_batches WHERE id = ?",
            (roast_batch_id,), one=True
        )

        if not batch:
            flash('Roast batch not found', 'error')
            return redirect(url_for('roast_tracker.production'))

        # Determine package size and total coffee needed
        package_sizes = {
            'whole_bean_16': 16,
            'whole_bean_70': 70,
            'whole_bean_250': 250,
            'drip_11': 11,
        }

        if production_type in package_sizes:
            package_size = package_sizes[production_type]
            total_needed = package_size * quantity
        elif production_type in ('cold_brew', 'market', 'sampling'):
            package_size = None
            total_needed = custom_weight or 0
        else:
            flash('Invalid production type', 'error')
            return redirect(url_for('roast_tracker.production'))

        # Check available stock
        if total_needed > batch['available_weight_g']:
            flash(f'Not enough stock. Available: {batch["available_weight_g"]}g, Needed: {total_needed}g', 'error')
            return redirect(url_for('roast_tracker.production'))

        # Generate production LOT
        prod_date = date.today()
        if production_type == 'drip_11':
            prod_lot = generate_drip_lot(batch['roast_level'], prod_date)
        elif production_type == 'cold_brew':
            prod_lot = generate_cold_brew_lot(prod_date)
        else:
            # Whole bean uses same LOT as roast
            prod_lot = batch['lot_number']

        # Create production batch
        conn = get_db()
        cur = conn.cursor()

        cur.execute("""
            INSERT INTO production_batches (
                production_lot, production_type, package_size_g, quantity,
                total_coffee_used_g, production_date, notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (prod_lot, production_type, package_size, quantity, total_needed, prod_date.isoformat(), notes))

        prod_batch_id = cur.lastrowid

        # Link to source roast
        cur.execute("""
            INSERT INTO production_sources (production_batch_id, roast_batch_id, weight_used_g)
            VALUES (?, ?, ?)
        """, (prod_batch_id, roast_batch_id, total_needed))

        # Deduct from available stock
        cur.execute("""
            UPDATE roast_batches
            SET available_weight_g = available_weight_g - ?
            WHERE id = ?
        """, (total_needed, roast_batch_id))

        conn.commit()
        conn.close()

        flash(f'Production recorded: {quantity}x {production_type} ({total_needed}g) from {batch["lot_number"]}', 'success')
        return redirect(url_for('roast_tracker.dashboard'))

    # GET: Show production form
    # Get batches with available stock
    batches = query_db("""
        SELECT rb.*, cp.name as product_name, gc.country
        FROM roast_batches rb
        JOIN coffee_products cp ON rb.product_id = cp.id
        LEFT JOIN green_coffee gc ON cp.green_coffee_id = gc.id
        WHERE rb.available_weight_g > 0
        ORDER BY rb.roast_date DESC
    """)

    return render_template('roast_tracker/production.html',
                           batches=batches,
                           roast_levels=ROAST_LEVELS)


@roast_tracker.route('/advent', methods=['GET', 'POST'])
@tracker_login_required
def advent_calendar():
    """Advent calendar production - 4 light roasts + 4 medium roasts"""
    if request.method == 'POST':
        advent_date_str = request.form.get('advent_date')
        advent_date = datetime.strptime(advent_date_str, '%Y-%m-%d').date()
        calendar_year = advent_date.year

        # Generate advent LOT
        advent_lot = generate_advent_lot(advent_date)

        conn = get_db()
        cur = conn.cursor()

        total_used = 0
        slot_number = 0

        # Process 4 light roast selections
        for i in range(1, 5):
            batch_id = request.form.get(f'light_{i}_batch', type=int)
            weight = request.form.get(f'light_{i}_weight', type=float, default=0)

            if batch_id and weight > 0:
                batch = query_db("SELECT * FROM roast_batches WHERE id = ?", (batch_id,), one=True)
                if batch and batch['available_weight_g'] >= weight:
                    slot_number += 1
                    cur.execute("""
                        INSERT INTO advent_calendar_contents (advent_lot, calendar_year, day_number, roast_batch_id, weight_g)
                        VALUES (?, ?, ?, ?, ?)
                    """, (advent_lot, calendar_year, slot_number, batch_id, weight))

                    cur.execute("""
                        UPDATE roast_batches SET available_weight_g = available_weight_g - ? WHERE id = ?
                    """, (weight, batch_id))

                    total_used += weight

        # Process 4 medium roast selections
        for i in range(1, 5):
            batch_id = request.form.get(f'medium_{i}_batch', type=int)
            weight = request.form.get(f'medium_{i}_weight', type=float, default=0)

            if batch_id and weight > 0:
                batch = query_db("SELECT * FROM roast_batches WHERE id = ?", (batch_id,), one=True)
                if batch and batch['available_weight_g'] >= weight:
                    slot_number += 1
                    cur.execute("""
                        INSERT INTO advent_calendar_contents (advent_lot, calendar_year, day_number, roast_batch_id, weight_g)
                        VALUES (?, ?, ?, ?, ?)
                    """, (advent_lot, calendar_year, slot_number, batch_id, weight))

                    cur.execute("""
                        UPDATE roast_batches SET available_weight_g = available_weight_g - ? WHERE id = ?
                    """, (weight, batch_id))

                    total_used += weight

        # Create production batch entry
        cur.execute("""
            INSERT INTO production_batches (production_lot, production_type, quantity, total_coffee_used_g, production_date)
            VALUES (?, 'advent', 1, ?, ?)
        """, (advent_lot, total_used, advent_date.isoformat()))

        conn.commit()
        conn.close()

        flash(f'Advent calendar created: {advent_lot} ({total_used}g total)', 'success')
        return redirect(url_for('roast_tracker.dashboard'))

    # GET: Show advent form
    # Each selection needs 48g (3 days × 16g)
    MIN_WEIGHT_FOR_ADVENT = 48

    # Get light roasts (V) with at least 48g available
    light_batches = query_db("""
        SELECT rb.*, cp.name as product_name, gc.country
        FROM roast_batches rb
        JOIN coffee_products cp ON rb.product_id = cp.id
        LEFT JOIN green_coffee gc ON cp.green_coffee_id = gc.id
        WHERE rb.available_weight_g >= ? AND rb.roast_level = 'V'
        ORDER BY gc.country, rb.roast_date DESC
    """, (MIN_WEIGHT_FOR_ADVENT,))

    # Get medium roasts (K) with at least 48g available
    medium_batches = query_db("""
        SELECT rb.*, cp.name as product_name, gc.country
        FROM roast_batches rb
        JOIN coffee_products cp ON rb.product_id = cp.id
        LEFT JOIN green_coffee gc ON cp.green_coffee_id = gc.id
        WHERE rb.available_weight_g >= ? AND rb.roast_level = 'K'
        ORDER BY gc.country, rb.roast_date DESC
    """, (MIN_WEIGHT_FOR_ADVENT,))

    return render_template('roast_tracker/advent_calendar.html',
                           light_batches=light_batches,
                           medium_batches=medium_batches,
                           today=date.today().isoformat())


@roast_tracker.route('/inventory')
@tracker_login_required
def inventory():
    """Full inventory view"""
    # All batches
    batches = query_db("""
        SELECT rb.*, cp.name as product_name, gc.country,
               (rb.roasted_weight_g - rb.available_weight_g) as used_weight_g
        FROM roast_batches rb
        JOIN coffee_products cp ON rb.product_id = cp.id
        LEFT JOIN green_coffee gc ON cp.green_coffee_id = gc.id
        ORDER BY rb.roast_date DESC
    """)

    # Production history
    production = query_db("""
        SELECT pb.*, GROUP_CONCAT(rb.lot_number) as source_lots
        FROM production_batches pb
        LEFT JOIN production_sources ps ON pb.id = ps.production_batch_id
        LEFT JOIN roast_batches rb ON ps.roast_batch_id = rb.id
        GROUP BY pb.id
        ORDER BY pb.production_date DESC
    """)

    # All coffee products (offerings) with inventory summary
    products = query_db("""
        SELECT cp.*, gc.country, gc.name as green_name,
               COALESCE(SUM(rb.available_weight_g), 0) as total_available_g,
               COUNT(rb.id) as batch_count
        FROM coffee_products cp
        LEFT JOIN green_coffee gc ON cp.green_coffee_id = gc.id
        LEFT JOIN roast_batches rb ON cp.id = rb.product_id AND rb.available_weight_g > 0
        WHERE cp.is_active = 1
        GROUP BY cp.id
        ORDER BY gc.country, cp.name
    """)

    return render_template('roast_tracker/inventory.html',
                           batches=batches,
                           production=production,
                           products=products,
                           roast_levels=ROAST_LEVELS,
                           today=date.today().isoformat())


@roast_tracker.route('/batch/<int:batch_id>')
@tracker_login_required
def batch_detail(batch_id):
    """View details of a roast batch"""
    batch = query_db("""
        SELECT rb.*, cp.name as product_name, gc.country, gc.process, gc.tasting_notes
        FROM roast_batches rb
        JOIN coffee_products cp ON rb.product_id = cp.id
        LEFT JOIN green_coffee gc ON cp.green_coffee_id = gc.id
        WHERE rb.id = ?
    """, (batch_id,), one=True)

    if not batch:
        flash('Batch not found', 'error')
        return redirect(url_for('roast_tracker.inventory'))

    # Production from this batch
    production = query_db("""
        SELECT pb.*
        FROM production_batches pb
        JOIN production_sources ps ON pb.id = ps.production_batch_id
        WHERE ps.roast_batch_id = ?
        ORDER BY pb.production_date DESC
    """, (batch_id,))

    # RoastTime data if available
    roasttime_data = None
    if batch['roasttime_uid']:
        roasttime_data = get_roast_by_uid(batch['roasttime_uid'])

    return render_template('roast_tracker/batch_detail.html',
                           batch=batch,
                           production=production,
                           roasttime_data=roasttime_data,
                           roast_levels=ROAST_LEVELS)


# ===== API Endpoints =====

@roast_tracker.route('/api/batches')
@tracker_login_required
def api_batches():
    """API: Get all batches with stock"""
    batches = query_db("""
        SELECT rb.*, cp.name as product_name, gc.country
        FROM roast_batches rb
        JOIN coffee_products cp ON rb.product_id = cp.id
        LEFT JOIN green_coffee gc ON cp.green_coffee_id = gc.id
        WHERE rb.available_weight_g > 0
        ORDER BY rb.roast_date DESC
    """)
    return jsonify([dict(b) for b in batches])


@roast_tracker.route('/api/roasttime')
@tracker_login_required
def api_roasttime():
    """API: Get RoastTime roasts for import"""
    limit = request.args.get('limit', 50, type=int)
    roasts = load_all_roasts()[:limit]

    # Simplify for JSON
    return jsonify([{
        'uid': r['roasttime_uid'],
        'name': r['roast_name'],
        'date': r['roast_date'].isoformat() if r['roast_date'] else None,
        'green_weight_g': r['green_weight_g'],
        'roasted_weight_g': r['roasted_weight_g'],
        'weight_loss_percent': r['weight_loss_percent'],
        'drop_temp': r['drop_temp'],
        'total_roast_time': r['total_roast_time'],
        'guessed_level': guess_roast_level(r)
    } for r in roasts])


@roast_tracker.route('/api/generate-lot', methods=['POST'])
@tracker_login_required
def api_generate_lot():
    """API: Generate LOT number preview"""
    from .lot_generator import get_next_sequence, format_date_part

    data = request.json
    roast_level = data.get('roast_level')
    roast_date_str = data.get('roast_date')
    product_id = data.get('product_id')
    custom_sequence = data.get('custom_sequence')

    roast_date = datetime.strptime(roast_date_str, '%Y-%m-%d').date()

    # Get the next sequence for display
    next_sequence = get_next_sequence(roast_level, roast_date)

    # Generate LOT with custom sequence if provided
    if custom_sequence:
        lot = generate_roast_lot(roast_level, roast_date, product_id, int(custom_sequence))
    else:
        lot = generate_roast_lot(roast_level, roast_date, product_id)

    return jsonify({
        'lot_number': lot,
        'next_sequence': next_sequence,
        'date_part': format_date_part(roast_date)
    })


# ===== Setup Routes =====

@roast_tracker.route('/setup/products', methods=['GET'])
@tracker_login_required
def setup_products():
    """Setup: Manage coffee products"""
    green_coffees = query_db("SELECT * FROM green_coffee ORDER BY country, name")
    products = query_db("""
        SELECT cp.*, gc.country, gc.name as green_coffee_name
        FROM coffee_products cp
        LEFT JOIN green_coffee gc ON cp.green_coffee_id = gc.id
        ORDER BY gc.country, cp.name
    """)

    return render_template('roast_tracker/setup_products.html',
                           green_coffees=green_coffees,
                           products=products,
                           roast_levels=ROAST_LEVELS)


@roast_tracker.route('/setup/green-coffee', methods=['POST'])
@tracker_login_required
def add_green_coffee():
    """Add or update green coffee"""
    coffee_id = request.form.get('green_coffee_id', type=int)
    name = request.form.get('name')
    country = request.form.get('country')
    region = request.form.get('region', '')
    process = request.form.get('process', '')
    stock_kg = request.form.get('stock_kg', type=float, default=0) or 0
    tasting_notes = request.form.get('tasting_notes', '')

    if coffee_id:
        # Update existing
        query_db("""
            UPDATE green_coffee
            SET name = ?, country = ?, region = ?, process = ?, current_stock_kg = ?, tasting_notes = ?
            WHERE id = ?
        """, (name, country, region, process, stock_kg, tasting_notes, coffee_id))
        flash('Green coffee updated', 'success')
    else:
        # Insert new
        query_db("""
            INSERT INTO green_coffee (name, country, region, process, current_stock_kg, tasting_notes)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (name, country, region, process, stock_kg, tasting_notes))
        flash('Green coffee added', 'success')

    return redirect(url_for('roast_tracker.setup_products'))


@roast_tracker.route('/setup/product', methods=['POST'])
@tracker_login_required
def add_product():
    """Add or update coffee product"""
    product_id = request.form.get('product_id', type=int)
    name = request.form.get('name')
    green_coffee_id = request.form.get('green_coffee_id', type=int)
    roast_level = request.form.get('roast_level')

    if product_id:
        # Update existing
        query_db("""
            UPDATE coffee_products
            SET name = ?, green_coffee_id = ?, roast_level = ?
            WHERE id = ?
        """, (name, green_coffee_id, roast_level, product_id))
        flash('Product updated', 'success')
    else:
        # Insert new
        query_db("""
            INSERT INTO coffee_products (name, green_coffee_id, roast_level)
            VALUES (?, ?, ?)
        """, (name, green_coffee_id, roast_level))
        flash('Product added', 'success')

    return redirect(url_for('roast_tracker.setup_products'))


@roast_tracker.route('/api/green-coffee/<int:coffee_id>')
@tracker_login_required
def api_get_green_coffee(coffee_id):
    """API: Get green coffee details"""
    coffee = query_db("SELECT * FROM green_coffee WHERE id = ?", (coffee_id,), one=True)
    if coffee:
        return jsonify(dict(coffee))
    return jsonify({'error': 'Not found'}), 404


@roast_tracker.route('/api/product/<int:product_id>')
@tracker_login_required
def api_get_product(product_id):
    """API: Get product details"""
    product = query_db("SELECT * FROM coffee_products WHERE id = ?", (product_id,), one=True)
    if product:
        return jsonify(dict(product))
    return jsonify({'error': 'Not found'}), 404


# ===== Orders & Fulfillment =====

@roast_tracker.route('/orders')
@tracker_login_required
def orders():
    """View WooCommerce orders and fulfillment status"""
    import sys
    sys.path.insert(0, '..')
    from app import fetch_wc_orders

    # Fetch processing orders from WooCommerce
    wc_orders = fetch_wc_orders(status='processing')

    # Get available packaged products
    packaged = query_db("""
        SELECT
            pb.id, pb.production_lot, pb.production_type, pb.package_size_g, pb.quantity,
            rb.lot_number, rb.roast_level,
            cp.name as product_name, gc.country
        FROM production_batches pb
        JOIN production_sources ps ON pb.id = ps.production_batch_id
        JOIN roast_batches rb ON ps.roast_batch_id = rb.id
        JOIN coffee_products cp ON rb.product_id = cp.id
        LEFT JOIN green_coffee gc ON cp.green_coffee_id = gc.id
        WHERE pb.production_type IN ('whole_bean_250', 'whole_bean_70', 'whole_bean_16', 'drip_11')
          AND pb.quantity > 0
        ORDER BY cp.name, pb.package_size_g DESC
    """)

    # Get available roasted coffee (for fulfillment analysis)
    roasted = query_db("""
        SELECT rb.*, cp.name as product_name, gc.country
        FROM roast_batches rb
        JOIN coffee_products cp ON rb.product_id = cp.id
        LEFT JOIN green_coffee gc ON cp.green_coffee_id = gc.id
        WHERE rb.available_weight_g > 0
        ORDER BY cp.name, rb.roast_date DESC
    """)

    # Analyze fulfillment for each order
    for order in wc_orders:
        order['fulfillment_status'] = 'ready'  # Default
        order['missing_items'] = []

        for item in order['line_items']:
            item_name = item['name'].lower()
            item_qty = item['quantity']

            # Check if we have packaged product
            found_package = False
            for pkg in packaged:
                pkg_name = pkg['product_name'].lower()
                if pkg_name in item_name or item_name in pkg_name:
                    if pkg['quantity'] >= item_qty:
                        found_package = True
                        item['fulfillment'] = 'packaged'
                        break

            if not found_package:
                # Check if we have roasted coffee to package
                found_roasted = False
                for rb in roasted:
                    rb_name = rb['product_name'].lower()
                    if rb_name in item_name or item_name in rb_name:
                        # Estimate weight needed (250g default)
                        weight_needed = item_qty * 250
                        if rb['available_weight_g'] >= weight_needed:
                            found_roasted = True
                            item['fulfillment'] = 'needs_packaging'
                            break

                if not found_roasted:
                    item['fulfillment'] = 'needs_roasting'
                    order['fulfillment_status'] = 'incomplete'
                    order['missing_items'].append(item['name'])

    return render_template('roast_tracker/orders.html',
                           orders=wc_orders,
                           packaged=packaged,
                           roasted=roasted)


@roast_tracker.route('/advent-config', methods=['GET', 'POST'])
@tracker_login_required
def advent_config():
    """Configure which coffees go into advent calendars"""
    if request.method == 'POST':
        conn = get_db()
        cur = conn.cursor()

        # Clear existing config
        cur.execute("DELETE FROM advent_calendar_config")

        # Save new config
        for i in range(1, 5):
            light_product_id = request.form.get(f'light_{i}', type=int)
            medium_product_id = request.form.get(f'medium_{i}', type=int)

            if light_product_id:
                cur.execute("""
                    INSERT INTO advent_calendar_config (slot_number, roast_type, product_id)
                    VALUES (?, 'light', ?)
                """, (i, light_product_id))

            if medium_product_id:
                cur.execute("""
                    INSERT INTO advent_calendar_config (slot_number, roast_type, product_id)
                    VALUES (?, 'medium', ?)
                """, (i, medium_product_id))

        conn.commit()
        conn.close()
        flash('Advent calendar configuration saved', 'success')
        return redirect(url_for('roast_tracker.advent_config'))

    # GET: Show config form
    # Get all light roast products
    light_products = query_db("""
        SELECT cp.*, gc.country
        FROM coffee_products cp
        LEFT JOIN green_coffee gc ON cp.green_coffee_id = gc.id
        WHERE cp.roast_level = 'V' AND cp.is_active = 1
        ORDER BY gc.country, cp.name
    """)

    # Get all medium roast products
    medium_products = query_db("""
        SELECT cp.*, gc.country
        FROM coffee_products cp
        LEFT JOIN green_coffee gc ON cp.green_coffee_id = gc.id
        WHERE cp.roast_level = 'K' AND cp.is_active = 1
        ORDER BY gc.country, cp.name
    """)

    # Get current config
    current_config = query_db("""
        SELECT ac.*, cp.name as product_name, gc.country
        FROM advent_calendar_config ac
        JOIN coffee_products cp ON ac.product_id = cp.id
        LEFT JOIN green_coffee gc ON cp.green_coffee_id = gc.id
        WHERE ac.is_active = 1
        ORDER BY ac.roast_type, ac.slot_number
    """)

    # Organize current config by slot
    config_by_slot = {'light': {}, 'medium': {}}
    for cfg in current_config:
        config_by_slot[cfg['roast_type']][cfg['slot_number']] = cfg['product_id']

    # Check inventory for each configured product
    inventory_status = {}
    for cfg in current_config:
        # Check if we have 48g+ of roasted coffee
        roasted = query_db("""
            SELECT SUM(available_weight_g) as total
            FROM roast_batches
            WHERE product_id = ? AND available_weight_g > 0
        """, (cfg['product_id'],), one=True)

        total_available = roasted['total'] if roasted and roasted['total'] else 0
        # Use string keys for JSON compatibility
        inventory_status[str(cfg['product_id'])] = {
            'available_g': int(total_available),
            'enough_for_advent': total_available >= 48
        }

    return render_template('roast_tracker/advent_config.html',
                           light_products=light_products,
                           medium_products=medium_products,
                           config_by_slot=config_by_slot,
                           inventory_status=inventory_status)


@roast_tracker.route('/roast-plan', methods=['GET', 'POST'])
@tracker_login_required
def roast_plan():
    """View and manage roast plan"""
    if request.method == 'POST':
        action = request.form.get('action')

        if action == 'add':
            product_id = request.form.get('product_id', type=int)
            planned_weight = request.form.get('planned_weight_g', type=float)
            planned_date = request.form.get('planned_date')
            notes = request.form.get('notes', '')
            source = request.form.get('source', 'manual')

            query_db("""
                INSERT INTO roast_plans (product_id, planned_green_weight_g, planned_date, notes, source)
                VALUES (?, ?, ?, ?, ?)
            """, (product_id, planned_weight, planned_date, notes, source))
            flash('Added to roast plan', 'success')

        elif action == 'complete':
            plan_id = request.form.get('plan_id', type=int)
            query_db("UPDATE roast_plans SET status = 'completed' WHERE id = ?", (plan_id,))
            flash('Plan item marked as completed', 'success')

        elif action == 'cancel':
            plan_id = request.form.get('plan_id', type=int)
            query_db("UPDATE roast_plans SET status = 'cancelled' WHERE id = ?", (plan_id,))
            flash('Plan item cancelled', 'success')

        elif action == 'delete':
            plan_id = request.form.get('plan_id', type=int)
            query_db("DELETE FROM roast_plans WHERE id = ?", (plan_id,))
            flash('Plan item deleted', 'success')

        return redirect(url_for('roast_tracker.roast_plan'))

    # GET: Show roast plan
    plans = query_db("""
        SELECT rp.*, cp.name as product_name, gc.country
        FROM roast_plans rp
        JOIN coffee_products cp ON rp.product_id = cp.id
        LEFT JOIN green_coffee gc ON cp.green_coffee_id = gc.id
        WHERE rp.status = 'planned'
        ORDER BY rp.planned_date, cp.name
    """)

    # Get all products for adding new items
    products = query_db("""
        SELECT cp.*, gc.country
        FROM coffee_products cp
        LEFT JOIN green_coffee gc ON cp.green_coffee_id = gc.id
        WHERE cp.is_active = 1
        ORDER BY gc.country, cp.name
    """)

    return render_template('roast_tracker/roast_plan.html',
                           plans=plans,
                           products=products,
                           today=date.today().isoformat())


@roast_tracker.route('/api/add-to-plan', methods=['POST'])
@tracker_login_required
def api_add_to_plan():
    """API: Add product to roast plan"""
    data = request.json
    product_id = data.get('product_id')
    planned_weight = data.get('planned_weight_g', 888)  # 888g green -> ~750g roasted
    planned_date = data.get('planned_date', date.today().isoformat())
    notes = data.get('notes', '')
    source = data.get('source', 'manual')

    query_db("""
        INSERT INTO roast_plans (product_id, planned_green_weight_g, planned_date, notes, source)
        VALUES (?, ?, ?, ?, ?)
    """, (product_id, planned_weight, planned_date, notes, source))

    return jsonify({'status': 'success', 'message': 'Added to roast plan'})


@roast_tracker.route('/api/analyze-orders', methods=['POST'])
@tracker_login_required
def api_analyze_orders():
    """API: Analyze orders and generate roast plan suggestions"""
    import sys
    sys.path.insert(0, '..')
    from app import fetch_wc_orders

    # Fetch processing orders
    orders = fetch_wc_orders(status='processing')

    # Get ALL products with their available roasted coffee
    products_with_stock = query_db("""
        SELECT cp.id, cp.name, cp.roast_level, gc.country,
               COALESCE(SUM(rb.available_weight_g), 0) as total_available
        FROM coffee_products cp
        LEFT JOIN green_coffee gc ON cp.green_coffee_id = gc.id
        LEFT JOIN roast_batches rb ON cp.id = rb.product_id
        WHERE cp.is_active = 1
        GROUP BY cp.id
    """)

    roasted_by_product = {p['id']: p['total_available'] for p in products_with_stock}
    products_by_id = {p['id']: p for p in products_with_stock}

    # Get advent calendar config requirements
    advent_config = query_db("""
        SELECT ac.product_id, cp.name
        FROM advent_calendar_config ac
        JOIN coffee_products cp ON ac.product_id = cp.id
        WHERE ac.is_active = 1
    """)

    # Calculate needs
    needs = {}  # product_id -> weight_needed

    # From orders (estimate 250g per order item)
    for order in orders:
        for item in order['line_items']:
            item_name = item['name'].lower()
            # Try to match product by name (more flexible matching)
            for p in products_with_stock:
                p_name = p['name'].lower()
                p_country = (p['country'] or '').lower()
                # Match if product name is in item name or vice versa
                if p_name in item_name or item_name in p_name or (p_country and p_country in item_name):
                    weight_needed = item['quantity'] * 250
                    needs[p['id']] = needs.get(p['id'], 0) + weight_needed
                    break

    # From advent calendar (48g per configured product)
    for ac in advent_config:
        needs[ac['product_id']] = needs.get(ac['product_id'], 0) + 48

    # Standard roast size: 888g green -> ~750g roasted (15% loss)
    STANDARD_ROAST_GREEN_G = 888
    LOW_STOCK_THRESHOLD = 300

    suggestions = []

    # First, add products with explicit needs that exceed available
    for product_id, needed in needs.items():
        available = roasted_by_product.get(product_id, 0)
        if needed > available:
            shortfall = needed - available
            product = products_by_id.get(product_id)
            if product:
                suggestions.append({
                    'product_id': product_id,
                    'product_name': product['name'],
                    'needed_g': needed,
                    'available_g': int(available),
                    'shortfall_g': shortfall,
                    'suggested_roast_g': STANDARD_ROAST_GREEN_G,
                    'reason': 'order_need'
                })

    # Also include all products with low stock (<300g) that aren't already in suggestions
    added_product_ids = {s['product_id'] for s in suggestions}
    for p in products_with_stock:
        if p['id'] not in added_product_ids and p['total_available'] < LOW_STOCK_THRESHOLD:
            suggestions.append({
                'product_id': p['id'],
                'product_name': p['name'],
                'needed_g': LOW_STOCK_THRESHOLD,  # Target stock level
                'available_g': int(p['total_available']),
                'shortfall_g': LOW_STOCK_THRESHOLD - int(p['total_available']),
                'suggested_roast_g': STANDARD_ROAST_GREEN_G,
                'reason': 'low_stock'
            })

    return jsonify({
        'status': 'success',
        'suggestions': suggestions,
        'order_count': len(orders)
    })


@roast_tracker.route('/api/refresh-order-statuses', methods=['POST'])
@tracker_login_required
def api_refresh_order_statuses():
    """API: Fetch current WooCommerce order statuses"""
    import sys
    sys.path.insert(0, '..')
    from app import wc_api_request

    order_ids = request.json.get('order_ids', [])

    if not order_ids:
        return jsonify({'status': 'error', 'message': 'No order IDs provided'}), 400

    statuses = {}
    for order_id in order_ids:
        try:
            order_data = wc_api_request(f'orders/{order_id}')
            if order_data:
                statuses[order_id] = order_data.get('status', 'unknown')
        except Exception:
            statuses[order_id] = 'unknown'

    return jsonify({
        'status': 'success',
        'order_statuses': statuses
    })


@roast_tracker.route('/api/adjust-inventory', methods=['POST'])
def api_adjust_inventory():
    """Adjust inventory for a product with audit trail"""
    data = request.json
    product_id = data.get('product_id')
    adjustment_type = data.get('adjustment_type', 'set')  # add, subtract, set, correction
    amount_g = data.get('amount_g')
    batch_id = data.get('batch_id')  # optional - specific batch to adjust
    comment = data.get('comment', '').strip()

    if not product_id or amount_g is None:
        return jsonify({'status': 'error', 'message': 'product_id and amount_g are required'}), 400

    if not comment:
        return jsonify({'status': 'error', 'message': 'Comment is required for audit trail'}), 400

    if adjustment_type not in ('add', 'subtract', 'set', 'correction'):
        return jsonify({'status': 'error', 'message': 'Invalid adjustment_type'}), 400

    conn = get_db()
    cur = conn.cursor()

    try:
        # Get current total available for product
        result = cur.execute("""
            SELECT COALESCE(SUM(available_weight_g), 0) as total
            FROM roast_batches WHERE product_id = ?
        """, (product_id,)).fetchone()
        previous_total = result['total'] if result else 0

        if batch_id:
            # Adjust specific batch
            batch = cur.execute("""
                SELECT available_weight_g FROM roast_batches WHERE id = ? AND product_id = ?
            """, (batch_id, product_id)).fetchone()

            if not batch:
                return jsonify({'status': 'error', 'message': 'Batch not found'}), 404

            current_batch_weight = batch['available_weight_g']

            if adjustment_type == 'add':
                new_batch_weight = current_batch_weight + amount_g
            elif adjustment_type == 'subtract':
                new_batch_weight = max(0, current_batch_weight - amount_g)
            elif adjustment_type in ('set', 'correction'):
                new_batch_weight = amount_g

            cur.execute("""
                UPDATE roast_batches SET available_weight_g = ? WHERE id = ?
            """, (new_batch_weight, batch_id))

            # Calculate new total
            result = cur.execute("""
                SELECT COALESCE(SUM(available_weight_g), 0) as total
                FROM roast_batches WHERE product_id = ?
            """, (product_id,)).fetchone()
            new_total = result['total']

        else:
            # Adjust product total - distribute across batches proportionally or use oldest first
            # Helper function to create an adjustment batch if none exists
            def create_adjustment_batch(weight_g):
                from datetime import date
                today = date.today()
                # Generate a LOT number for the adjustment: ADJ-YYMMDD-N
                lot_prefix = f"ADJ-{today.strftime('%y%m%d')}"
                existing = cur.execute("""
                    SELECT COUNT(*) as cnt FROM roast_batches
                    WHERE lot_number LIKE ?
                """, (f"{lot_prefix}%",)).fetchone()
                seq = (existing['cnt'] or 0) + 1
                lot_number = f"{lot_prefix}-{seq}"

                # Get product's roast level
                product = cur.execute("SELECT roast_level FROM coffee_products WHERE id = ?",
                                     (product_id,)).fetchone()
                roast_level = product['roast_level'] if product else 'K'

                cur.execute("""
                    INSERT INTO roast_batches
                    (lot_number, product_id, roast_date, roast_level, day_sequence,
                     green_weight_g, roasted_weight_g, available_weight_g, notes)
                    VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?)
                """, (lot_number, product_id, today.isoformat(), roast_level,
                      weight_g, weight_g, weight_g, f"Manual inventory adjustment: {comment}"))
                return cur.lastrowid

            if adjustment_type == 'add':
                # Find the newest batch and add to it
                newest_batch = cur.execute("""
                    SELECT id, available_weight_g FROM roast_batches
                    WHERE product_id = ?
                    ORDER BY roast_date DESC LIMIT 1
                """, (product_id,)).fetchone()

                if newest_batch:
                    new_weight = newest_batch['available_weight_g'] + amount_g
                    cur.execute("UPDATE roast_batches SET available_weight_g = ? WHERE id = ?",
                               (new_weight, newest_batch['id']))
                else:
                    # No batch exists - create one
                    create_adjustment_batch(amount_g)
                new_total = previous_total + amount_g

            elif adjustment_type == 'subtract':
                # Subtract from oldest batches first (FIFO)
                remaining = amount_g
                batches = cur.execute("""
                    SELECT id, available_weight_g FROM roast_batches
                    WHERE product_id = ? AND available_weight_g > 0
                    ORDER BY roast_date ASC
                """, (product_id,)).fetchall()

                for batch in batches:
                    if remaining <= 0:
                        break
                    take = min(remaining, batch['available_weight_g'])
                    new_weight = batch['available_weight_g'] - take
                    cur.execute("UPDATE roast_batches SET available_weight_g = ? WHERE id = ?",
                               (new_weight, batch['id']))
                    remaining -= take

                new_total = max(0, previous_total - amount_g)

            elif adjustment_type in ('set', 'correction'):
                # Set total to specific amount - adjust newest batch or create one
                newest_batch = cur.execute("""
                    SELECT id, available_weight_g FROM roast_batches
                    WHERE product_id = ?
                    ORDER BY roast_date DESC LIMIT 1
                """, (product_id,)).fetchone()

                if newest_batch:
                    diff = amount_g - previous_total
                    new_weight = max(0, newest_batch['available_weight_g'] + diff)
                    cur.execute("UPDATE roast_batches SET available_weight_g = ? WHERE id = ?",
                               (new_weight, newest_batch['id']))
                elif amount_g > 0:
                    # No batch exists and setting to non-zero - create one
                    create_adjustment_batch(amount_g)
                new_total = amount_g

        # Record the adjustment in audit log
        cur.execute("""
            INSERT INTO inventory_adjustments
            (product_id, batch_id, adjustment_type, amount_g, previous_total_g, new_total_g, comment)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (product_id, batch_id, adjustment_type, amount_g, previous_total, new_total, comment))

        conn.commit()

        return jsonify({
            'status': 'success',
            'previous_total_g': previous_total,
            'new_total_g': new_total,
            'adjustment_id': cur.lastrowid
        })

    except Exception as e:
        conn.rollback()
        return jsonify({'status': 'error', 'message': str(e)}), 500
    finally:
        conn.close()


@roast_tracker.route('/api/inventory-history/<int:product_id>')
def api_inventory_history(product_id):
    """Get inventory adjustment history for a product"""
    adjustments = query_db("""
        SELECT ia.*, cp.name as product_name, rb.lot_number
        FROM inventory_adjustments ia
        JOIN coffee_products cp ON ia.product_id = cp.id
        LEFT JOIN roast_batches rb ON ia.batch_id = rb.id
        WHERE ia.product_id = ?
        ORDER BY ia.created_at DESC
        LIMIT 50
    """, (product_id,))

    return jsonify({
        'status': 'success',
        'adjustments': [dict(a) for a in adjustments]
    })


@roast_tracker.route('/api/inventory-history')
def api_all_inventory_history():
    """Get all recent inventory adjustments"""
    adjustments = query_db("""
        SELECT ia.*, cp.name as product_name, rb.lot_number
        FROM inventory_adjustments ia
        JOIN coffee_products cp ON ia.product_id = cp.id
        LEFT JOIN roast_batches rb ON ia.batch_id = rb.id
        ORDER BY ia.created_at DESC
        LIMIT 100
    """)

    return jsonify({
        'status': 'success',
        'adjustments': [dict(a) for a in adjustments]
    })


@roast_tracker.route('/api/order-lot-assignments/<int:order_id>')
@tracker_login_required
def api_get_order_lot_assignments(order_id):
    """Get LOT assignments for an order"""
    assignments = query_db("""
        SELECT ola.*, rb.lot_number, cp.name as product_name
        FROM order_lot_assignments ola
        JOIN roast_batches rb ON ola.roast_batch_id = rb.id
        JOIN coffee_products cp ON rb.product_id = cp.id
        WHERE ola.wc_order_id = ?
        ORDER BY ola.wc_order_item_id, ola.slot_number
    """, (order_id,))

    return jsonify({
        'status': 'success',
        'assignments': [dict(a) for a in assignments]
    })


@roast_tracker.route('/api/assign-lot', methods=['POST'])
@tracker_login_required
def api_assign_lot():
    """Assign a LOT to an order item slot"""
    data = request.json
    order_id = data.get('order_id')
    order_item_id = data.get('order_item_id')
    slot_number = data.get('slot_number', 1)
    batch_id = data.get('batch_id')
    weight_g = data.get('weight_g', 250)

    if not all([order_id, order_item_id, batch_id]):
        return jsonify({'status': 'error', 'message': 'order_id, order_item_id, and batch_id are required'}), 400

    conn = get_db()
    cur = conn.cursor()

    try:
        # Check if assignment already exists for this slot
        existing = cur.execute("""
            SELECT id FROM order_lot_assignments
            WHERE wc_order_id = ? AND wc_order_item_id = ? AND slot_number = ?
        """, (order_id, order_item_id, slot_number)).fetchone()

        if existing:
            # Update existing assignment
            cur.execute("""
                UPDATE order_lot_assignments
                SET roast_batch_id = ?, weight_g = ?, assigned_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (batch_id, weight_g, existing['id']))
        else:
            # Create new assignment
            cur.execute("""
                INSERT INTO order_lot_assignments
                (wc_order_id, wc_order_item_id, slot_number, roast_batch_id, weight_g)
                VALUES (?, ?, ?, ?, ?)
            """, (order_id, order_item_id, slot_number, batch_id, weight_g))

        conn.commit()

        # Get the lot number for the response
        batch = cur.execute("SELECT lot_number FROM roast_batches WHERE id = ?", (batch_id,)).fetchone()

        return jsonify({
            'status': 'success',
            'lot_number': batch['lot_number'] if batch else None
        })

    except Exception as e:
        conn.rollback()
        return jsonify({'status': 'error', 'message': str(e)}), 500
    finally:
        conn.close()


@roast_tracker.route('/api/remove-lot-assignment', methods=['POST'])
@tracker_login_required
def api_remove_lot_assignment():
    """Remove a LOT assignment from an order item slot"""
    data = request.json
    order_id = data.get('order_id')
    order_item_id = data.get('order_item_id')
    slot_number = data.get('slot_number', 1)

    if not all([order_id, order_item_id]):
        return jsonify({'status': 'error', 'message': 'order_id and order_item_id are required'}), 400

    query_db("""
        DELETE FROM order_lot_assignments
        WHERE wc_order_id = ? AND wc_order_item_id = ? AND slot_number = ?
    """, (order_id, order_item_id, slot_number))

    return jsonify({'status': 'success'})


@roast_tracker.route('/api/available-lots')
@tracker_login_required
def api_available_lots():
    """Get all available LOTs for assignment"""
    lots = query_db("""
        SELECT rb.id, rb.lot_number, rb.available_weight_g, rb.roast_date,
               cp.name as product_name, cp.roast_level, gc.country
        FROM roast_batches rb
        JOIN coffee_products cp ON rb.product_id = cp.id
        LEFT JOIN green_coffee gc ON cp.green_coffee_id = gc.id
        WHERE rb.available_weight_g > 0
        ORDER BY cp.name, rb.roast_date DESC
    """)

    return jsonify({
        'status': 'success',
        'lots': [dict(l) for l in lots]
    })


# Initialize database on first request
@roast_tracker.before_app_request
def ensure_db():
    """Ensure database exists"""
    from .database import DATABASE_PATH
    import os
    if not os.path.exists(DATABASE_PATH):
        init_db()
