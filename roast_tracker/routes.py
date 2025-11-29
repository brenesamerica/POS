"""
Flask routes for Roast Tracker
"""
import os
from flask import Blueprint, render_template, request, jsonify, redirect, url_for, flash
from datetime import datetime, date, timedelta
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
    # Includes BOTH unpacked roasted coffee AND packed products still in inventory
    # Excludes archived products
    LOW_STOCK_THRESHOLD = 300
    low_stock_products = query_db("""
        SELECT cp.id, cp.name as product_name, cp.roast_level,
               gc.country,
               COALESCE(SUM(rb.available_weight_g), 0) as roasted_available_g,
               COALESCE(
                   (SELECT SUM(pb.quantity * pb.package_size_g)
                    FROM production_batches pb
                    JOIN production_sources ps ON pb.id = ps.production_batch_id
                    JOIN roast_batches rb2 ON ps.roast_batch_id = rb2.id
                    WHERE rb2.product_id = cp.id AND pb.quantity > 0
                      AND pb.production_type IN ('whole_bean_250', 'whole_bean_70', 'whole_bean_16', 'drip_11')
                   ), 0
               ) as packed_available_g,
               COALESCE(SUM(rb.available_weight_g), 0) +
               COALESCE(
                   (SELECT SUM(pb.quantity * pb.package_size_g)
                    FROM production_batches pb
                    JOIN production_sources ps ON pb.id = ps.production_batch_id
                    JOIN roast_batches rb2 ON ps.roast_batch_id = rb2.id
                    WHERE rb2.product_id = cp.id AND pb.quantity > 0
                      AND pb.production_type IN ('whole_bean_250', 'whole_bean_70', 'whole_bean_16', 'drip_11')
                   ), 0
               ) as total_available_g
        FROM coffee_products cp
        LEFT JOIN green_coffee gc ON cp.green_coffee_id = gc.id
        LEFT JOIN roast_batches rb ON cp.id = rb.product_id
        WHERE cp.is_active = 1 AND COALESCE(cp.is_archived, 0) = 0
        GROUP BY cp.id
        HAVING total_available_g < ?
        ORDER BY total_available_g ASC
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

    # Get packed products (ready to ship) grouped by product
    # Sorted by origin (country), then roast level (V=light, K=medium, S=dark)
    packed_products = query_db("""
        SELECT
            cp.id as product_id,
            cp.name as product_name,
            cp.roast_level,
            gc.country,
            pb.package_size_g,
            SUM(pb.quantity) as total_quantity,
            GROUP_CONCAT(pb.production_lot || ':' || pb.quantity) as lots_detail
        FROM production_batches pb
        JOIN production_sources ps ON pb.id = ps.production_batch_id
        JOIN roast_batches rb ON ps.roast_batch_id = rb.id
        JOIN coffee_products cp ON rb.product_id = cp.id
        LEFT JOIN green_coffee gc ON cp.green_coffee_id = gc.id
        WHERE pb.quantity > 0
          AND pb.production_type IN ('whole_bean_250', 'whole_bean_70', 'whole_bean_16', 'drip_11')
        GROUP BY cp.id, pb.package_size_g
        ORDER BY gc.country,
                 CASE cp.roast_level
                     WHEN 'V' THEN 1
                     WHEN 'K' THEN 2
                     WHEN 'S' THEN 3
                     ELSE 4
                 END,
                 pb.package_size_g DESC
    """)

    # Calculate total packed weight
    total_packed = sum(p['total_quantity'] * p['package_size_g'] for p in packed_products) if packed_products else 0

    # Summary stats
    total_available = sum(b['available_weight_g'] for b in batches) if batches else 0

    return render_template('roast_tracker/dashboard.html',
                           batches=batches,
                           low_stock=low_stock_products,
                           recent_production=recent_production,
                           packed_products=packed_products,
                           total_available_kg=total_available / 1000,
                           total_packed_kg=total_packed / 1000,
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
        WHERE cp.is_active = 1 AND COALESCE(cp.is_archived, 0) = 0
        ORDER BY gc.country, cp.name
    """)

    # Get unique countries for filter
    countries = query_db("""
        SELECT DISTINCT gc.country
        FROM coffee_products cp
        LEFT JOIN green_coffee gc ON cp.green_coffee_id = gc.id
        WHERE cp.is_active = 1 AND COALESCE(cp.is_archived, 0) = 0 AND gc.country IS NOT NULL
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
    # Get batches with available stock, sorted by country then roast level (V=light, K=medium, S=dark)
    batches = query_db("""
        SELECT rb.*, cp.name as product_name, cp.roast_level, gc.country
        FROM roast_batches rb
        JOIN coffee_products cp ON rb.product_id = cp.id
        LEFT JOIN green_coffee gc ON cp.green_coffee_id = gc.id
        WHERE rb.available_weight_g > 0
        ORDER BY gc.country,
                 CASE cp.roast_level
                     WHEN 'V' THEN 1
                     WHEN 'K' THEN 2
                     WHEN 'S' THEN 3
                     ELSE 4
                 END,
                 rb.roast_date DESC
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

    # Packed products (ready to ship) - only show items with quantity > 0
    packed_products = query_db("""
        SELECT pb.id, pb.production_lot, pb.production_type, pb.package_size_g,
               pb.quantity, pb.production_date,
               rb.lot_number as source_lot, rb.roast_date,
               cp.name as product_name, cp.roast_level, gc.country
        FROM production_batches pb
        JOIN production_sources ps ON pb.id = ps.production_batch_id
        JOIN roast_batches rb ON ps.roast_batch_id = rb.id
        JOIN coffee_products cp ON rb.product_id = cp.id
        LEFT JOIN green_coffee gc ON cp.green_coffee_id = gc.id
        WHERE pb.quantity > 0
          AND pb.production_type IN ('whole_bean_250', 'whole_bean_70', 'whole_bean_16', 'drip_11')
        ORDER BY cp.name, pb.package_size_g DESC, pb.production_date DESC
    """)

    # All coffee products (offerings) with inventory summary
    # Exclude archived products from inventory view
    products = query_db("""
        SELECT cp.*, gc.country, gc.name as green_name,
               COALESCE(SUM(rb.available_weight_g), 0) as total_available_g,
               COUNT(rb.id) as batch_count
        FROM coffee_products cp
        LEFT JOIN green_coffee gc ON cp.green_coffee_id = gc.id
        LEFT JOIN roast_batches rb ON cp.id = rb.product_id AND rb.available_weight_g > 0
        WHERE cp.is_active = 1 AND COALESCE(cp.is_archived, 0) = 0
        GROUP BY cp.id
        ORDER BY gc.country, cp.name
    """)

    # Get batches (LOTs) per product for display in cards
    product_batches = {}
    for product in products:
        product_batches[product['id']] = query_db("""
            SELECT id, lot_number, available_weight_g, roast_date
            FROM roast_batches
            WHERE product_id = ? AND available_weight_g > 0
            ORDER BY roast_date DESC
        """, (product['id'],))

    return render_template('roast_tracker/inventory.html',
                           batches=batches,
                           production=production,
                           packed_products=packed_products,
                           products=products,
                           product_batches=product_batches,
                           roast_levels=ROAST_LEVELS,
                           today=date.today().isoformat())


@roast_tracker.route('/roast-history')
@tracker_login_required
def roast_history():
    """View all roast batches history"""
    # All batches
    batches = query_db("""
        SELECT rb.*, cp.name as product_name, gc.country,
               (rb.roasted_weight_g - rb.available_weight_g) as used_weight_g
        FROM roast_batches rb
        JOIN coffee_products cp ON rb.product_id = cp.id
        LEFT JOIN green_coffee gc ON cp.green_coffee_id = gc.id
        ORDER BY rb.roast_date DESC
    """)

    # Get unique countries for filter
    countries = query_db("""
        SELECT DISTINCT gc.country
        FROM roast_batches rb
        JOIN coffee_products cp ON rb.product_id = cp.id
        LEFT JOIN green_coffee gc ON cp.green_coffee_id = gc.id
        WHERE gc.country IS NOT NULL
        ORDER BY gc.country
    """)
    country_list = [c['country'] for c in countries if c['country']]

    # Calculate totals
    total_roasted = sum(b['roasted_weight_g'] for b in batches) if batches else 0
    total_available = sum(b['available_weight_g'] for b in batches) if batches else 0
    total_used = sum(b['used_weight_g'] for b in batches) if batches else 0

    return render_template('roast_tracker/roast_history.html',
                           batches=batches,
                           countries=country_list,
                           total_roasted_kg=total_roasted / 1000,
                           total_available_kg=total_available / 1000,
                           total_used_kg=total_used / 1000,
                           roast_levels=ROAST_LEVELS)


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
    show_archived = request.args.get('archived', '0') == '1'

    green_coffees = query_db("SELECT * FROM green_coffee ORDER BY country, name")

    if show_archived:
        products = query_db("""
            SELECT cp.*, gc.country, gc.name as green_coffee_name
            FROM coffee_products cp
            LEFT JOIN green_coffee gc ON cp.green_coffee_id = gc.id
            WHERE cp.is_archived = 1
            ORDER BY gc.country, cp.name
        """)
    else:
        products = query_db("""
            SELECT cp.*, gc.country, gc.name as green_coffee_name
            FROM coffee_products cp
            LEFT JOIN green_coffee gc ON cp.green_coffee_id = gc.id
            WHERE COALESCE(cp.is_archived, 0) = 0
            ORDER BY gc.country, cp.name
        """)

    return render_template('roast_tracker/setup_products.html',
                           green_coffees=green_coffees,
                           products=products,
                           roast_levels=ROAST_LEVELS,
                           show_archived=show_archived)


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


@roast_tracker.route('/api/product/<int:product_id>/archive', methods=['POST'])
@tracker_login_required
def archive_product(product_id):
    """Archive a coffee product"""
    query_db("UPDATE coffee_products SET is_archived = 1 WHERE id = ?", (product_id,))
    return jsonify({'status': 'success', 'message': 'Product archived'})


@roast_tracker.route('/api/product/<int:product_id>/unarchive', methods=['POST'])
@tracker_login_required
def unarchive_product(product_id):
    """Restore an archived coffee product"""
    query_db("UPDATE coffee_products SET is_archived = 0 WHERE id = ?", (product_id,))
    return jsonify({'status': 'success', 'message': 'Product restored'})


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

    # Fetch B2B orders that are pending/processing (not completed/cancelled)
    b2b_orders_raw = query_db("""
        SELECT o.*, c.company_name
        FROM b2b_orders o
        JOIN b2b_customers c ON o.customer_id = c.id
        WHERE o.status IN ('pending', 'processing', 'ready')
        ORDER BY o.order_date DESC
    """)

    # Convert B2B orders to a format similar to WooCommerce orders
    b2b_orders = []
    for order in b2b_orders_raw:
        # Get order items
        items = query_db("""
            SELECT * FROM b2b_order_items WHERE order_id = ? ORDER BY id
        """, (order['id'],))

        line_items = []
        for item in items:
            line_items.append({
                'id': item['id'],
                'name': item['product_name'],
                'quantity': item['quantity'],
                'product_id': item['product_id'],
                'package_size_g': item['package_size_g'],
                'fulfillment': 'unknown'
            })

        b2b_orders.append({
            'id': f"B2B-{order['id']}",  # Prefix to distinguish from WC orders
            'number': f"B2B-{order['id']}",
            'is_b2b': True,
            'b2b_id': order['id'],
            'status': order['status'],
            'date_created': order['order_date'],
            'total': order['total'],
            'currency': 'HUF',
            'billing': {
                'first_name': order['company_name'],
                'last_name': ''
            },
            'shipping': {
                'country': 'HU'
            },
            'line_items': line_items,
            'fulfillment_status': 'ready',
            'missing_items': []
        })

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

    # Get all LOT assignments to check which order items are already fulfilled
    lot_assignments = query_db("""
        SELECT wc_order_id, wc_order_item_id, COUNT(*) as assigned_slots
        FROM order_lot_assignments
        GROUP BY wc_order_id, wc_order_item_id
    """)
    # Build a dict: (order_id, item_id) -> assigned_slots
    # Note: wc_order_id can be numeric (WC orders) or string "B2B-x" (B2B orders)
    assignments_by_item = {}
    for a in lot_assignments:
        # Store with both possible key types for numeric IDs (int and str)
        order_id = a['wc_order_id']
        item_id = a['wc_order_item_id']
        assignments_by_item[(order_id, item_id)] = a['assigned_slots']
        # Also store string version of numeric IDs for consistent lookup
        if isinstance(order_id, int):
            assignments_by_item[(str(order_id), item_id)] = a['assigned_slots']

    # Combine WC orders and B2B orders
    all_orders = wc_orders + b2b_orders

    # Analyze fulfillment for each order
    for order in all_orders:
        order['fulfillment_status'] = 'ready'  # Default
        order['missing_items'] = []
        is_b2b = order.get('is_b2b', False)

        for item in order['line_items']:
            item_name = item['name'].lower()
            item_qty = item['quantity']

            # Calculate how many LOT slots are needed for this item
            # For B2B, use package_size_g; for WC, check the name
            if is_b2b:
                package_size = item.get('package_size_g', 250)
                slots_needed = (package_size // 250) * item_qty
            elif '500' in item['name']:
                slots_needed = 2 * item_qty
            else:
                slots_needed = item_qty

            # Use order['id'] directly - for B2B it's already "B2B-x", for WC it's numeric
            lookup_key = (order['id'], item['id'])

            # Check if LOT assignments already exist for this item
            assigned_slots = assignments_by_item.get(lookup_key, 0)
            if assigned_slots >= slots_needed:
                # Already fully assigned with LOT numbers
                item['fulfillment'] = 'packaged'
                continue

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

    # Calculate order summary - aggregate quantities per product (with 500g=2x250g logic)
    order_summary = {}
    for order in all_orders:
        is_b2b = order.get('is_b2b', False)
        for item in order['line_items']:
            item_name = item['name']
            item_qty = item['quantity']

            # Determine unit size from item - B2B has package_size_g, WC uses name
            if is_b2b:
                package_size = item.get('package_size_g', 250)
                unit_250g = (package_size // 250) * item_qty
            elif '500' in item_name:
                # 500g = 2x250g
                unit_250g = 2 * item_qty
            else:
                # Default 250g
                unit_250g = item_qty

            if item_name not in order_summary:
                order_summary[item_name] = {'total_qty': 0, 'total_250g': 0}
            order_summary[item_name]['total_qty'] += item_qty
            order_summary[item_name]['total_250g'] += unit_250g

    # Format summary for display (e.g., "4x250g")
    order_summary_formatted = []
    for name, data in sorted(order_summary.items()):
        qty_250g = data['total_250g']
        if qty_250g > 0:
            order_summary_formatted.append({
                'name': name,
                'display': f"{qty_250g}x250g",
                'total_g': qty_250g * 250
            })

    # Get WC invoices for all orders
    wc_invoices_list = query_db("SELECT wc_order_id, billingo_document_id as document_id FROM wc_order_invoices")
    wc_invoices = {str(inv['wc_order_id']): inv for inv in wc_invoices_list}

    return render_template('roast_tracker/orders.html',
                           orders=all_orders,
                           packaged=packaged,
                           roasted=roasted,
                           order_summary=order_summary_formatted,
                           wc_invoices=wc_invoices)


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
            'product_name': cfg['product_name'],
            'country': cfg['country'],
            'available_g': int(total_available),
            'enough_for_advent': total_available >= 48
        }

    return render_template('roast_tracker/advent_config.html',
                           light_products=light_products,
                           medium_products=medium_products,
                           config_by_slot=config_by_slot,
                           inventory_status=inventory_status,
                           today=date.today().isoformat())


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

            # Redirect back to referrer if specified
            redirect_to = request.form.get('redirect_to')
            if redirect_to:
                return redirect(redirect_to)

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
        SELECT rp.*, cp.name as product_name, cp.roast_level, gc.country
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


@roast_tracker.route('/api/preview-plan-lot', methods=['POST'])
@tracker_login_required
def api_preview_plan_lot():
    """Preview the LOT number that would be generated for a plan"""
    data = request.json
    plan_id = data.get('plan_id')
    roast_date_str = data.get('roast_date')

    if not plan_id:
        return jsonify({'status': 'error', 'message': 'plan_id is required'}), 400

    # Get the plan details
    plan = query_db("""
        SELECT rp.*, cp.roast_level, cp.name as product_name
        FROM roast_plans rp
        JOIN coffee_products cp ON rp.product_id = cp.id
        WHERE rp.id = ?
    """, (plan_id,), one=True)

    if not plan:
        return jsonify({'status': 'error', 'message': 'Plan not found'}), 404

    # Parse roast date
    from datetime import date
    if roast_date_str:
        roast_date = datetime.strptime(roast_date_str, '%Y-%m-%d').date()
    else:
        roast_date = date.today()

    roast_level = plan['roast_level'] or 'K'
    product_id = plan['product_id']

    # Generate LOT number preview
    lot_number = generate_roast_lot(roast_level, roast_date, product_id)

    return jsonify({
        'status': 'success',
        'lot_number': lot_number,
        'roast_level': roast_level,
        'product_name': plan['product_name']
    })


@roast_tracker.route('/api/complete-plan-with-roast', methods=['POST'])
@tracker_login_required
def api_complete_plan_with_roast():
    """Complete a roast plan by entering actual roast data - creates roast batch and LOT"""
    from .database import get_db
    from datetime import date

    data = request.json
    plan_id = data.get('plan_id')
    roasted_weight_g = data.get('roasted_weight_g')
    roast_date_str = data.get('roast_date', date.today().isoformat())
    notes = data.get('notes', '')
    custom_lot_number = data.get('lot_number')  # Allow custom LOT number

    if not plan_id or not roasted_weight_g:
        return jsonify({'status': 'error', 'message': 'plan_id and roasted_weight_g are required'}), 400

    # Get the plan details
    plan = query_db("""
        SELECT rp.*, cp.roast_level, cp.name as product_name
        FROM roast_plans rp
        JOIN coffee_products cp ON rp.product_id = cp.id
        WHERE rp.id = ?
    """, (plan_id,), one=True)

    if not plan:
        return jsonify({'status': 'error', 'message': 'Plan not found'}), 404

    if plan['status'] != 'planned':
        return jsonify({'status': 'error', 'message': 'Plan is not in planned status'}), 400

    # Parse roast date
    roast_date = datetime.strptime(roast_date_str, '%Y-%m-%d').date()

    # Get roast level from the product
    roast_level = plan['roast_level'] or 'K'
    product_id = plan['product_id']
    green_weight_g = plan['planned_green_weight_g']

    # Use custom LOT number if provided, otherwise generate
    if custom_lot_number:
        lot_number = custom_lot_number
    else:
        lot_number = generate_roast_lot(roast_level, roast_date, product_id)

    # Check if this LOT already exists (same product, same day, same level)
    existing = query_db(
        "SELECT id FROM roast_batches WHERE lot_number = ?",
        (lot_number,), one=True
    )

    conn = get_db()
    cur = conn.cursor()

    try:
        if existing:
            # Add to existing batch
            cur.execute("""
                UPDATE roast_batches
                SET green_weight_g = green_weight_g + ?,
                    roasted_weight_g = roasted_weight_g + ?,
                    available_weight_g = available_weight_g + ?
                WHERE lot_number = ?
            """, (green_weight_g, roasted_weight_g, roasted_weight_g, lot_number))
            batch_id = existing['id']
        else:
            # Calculate weight loss
            weight_loss = ((green_weight_g - roasted_weight_g) / green_weight_g * 100) if green_weight_g > 0 else 0

            # Get day sequence from LOT number
            day_seq = int(lot_number.split('/')[-1])

            # Combine notes from plan and any additional notes
            combined_notes = plan['notes'] or ''
            if notes:
                combined_notes = f"{combined_notes} | {notes}" if combined_notes else notes

            # Insert new roast batch
            cur.execute("""
                INSERT INTO roast_batches (
                    lot_number, product_id, roast_date, roast_level, day_sequence,
                    green_weight_g, roasted_weight_g, available_weight_g, weight_loss_percent, notes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                lot_number, product_id, roast_date.isoformat(), roast_level, day_seq,
                green_weight_g, roasted_weight_g, roasted_weight_g, weight_loss, combined_notes
            ))
            batch_id = cur.lastrowid

        # Mark plan as completed
        cur.execute("UPDATE roast_plans SET status = 'completed' WHERE id = ?", (plan_id,))

        conn.commit()

        return jsonify({
            'status': 'success',
            'message': f'Roast completed and added to inventory',
            'lot_number': lot_number,
            'batch_id': batch_id,
            'roasted_weight_g': roasted_weight_g,
            'weight_loss_percent': round(((green_weight_g - roasted_weight_g) / green_weight_g * 100), 1) if green_weight_g > 0 else 0
        })

    except Exception as e:
        conn.rollback()
        return jsonify({'status': 'error', 'message': str(e)}), 500
    finally:
        conn.close()


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
    """API: Analyze orders and generate roast plan suggestions

    Only suggests roasting for coffees that are ORDERED and where
    combined (packed + roasted) inventory is insufficient.

    Roast calculation: 888g green -> ~750g roasted (15.5% loss)
    Each batch is a separate roast plan entry (not combined).
    """
    import sys
    sys.path.insert(0, '..')
    from app import fetch_wc_orders
    import math

    # Fetch processing orders
    orders = fetch_wc_orders(status='processing')

    # Get ALL products with their info
    products = query_db("""
        SELECT cp.id, cp.name, cp.roast_level, gc.country
        FROM coffee_products cp
        LEFT JOIN green_coffee gc ON cp.green_coffee_id = gc.id
        WHERE cp.is_active = 1
    """)
    products_by_id = {p['id']: p for p in products}

    # Get ROASTED inventory (available weight in roast batches)
    roasted_inventory = query_db("""
        SELECT product_id, COALESCE(SUM(available_weight_g), 0) as roasted_g
        FROM roast_batches
        WHERE available_weight_g > 0
        GROUP BY product_id
    """)
    roasted_by_product = {r['product_id']: r['roasted_g'] for r in roasted_inventory}

    # Get PACKED inventory (available units * package size)
    packed_inventory = query_db("""
        SELECT cp.id as product_id,
               COALESCE(SUM(pb.quantity * pb.package_size_g), 0) as packed_g
        FROM coffee_products cp
        LEFT JOIN roast_batches rb ON cp.id = rb.product_id
        LEFT JOIN production_sources ps ON rb.id = ps.roast_batch_id
        LEFT JOIN production_batches pb ON ps.production_batch_id = pb.id AND pb.quantity > 0
        WHERE cp.is_active = 1
        GROUP BY cp.id
    """)
    packed_by_product = {p['product_id']: p['packed_g'] for p in packed_inventory}

    # Get existing LOT assignments to subtract from needs
    # (already assigned = already fulfilled, don't need to roast for these)
    existing_assignments = query_db("""
        SELECT wc_order_id, wc_order_item_id, slot_number, weight_g
        FROM order_lot_assignments
    """)
    # Build a dict: (order_id, item_id) -> total_assigned_weight_g
    assigned_by_item = {}
    for a in existing_assignments:
        key = (a['wc_order_id'], a['wc_order_item_id'])
        assigned_by_item[key] = assigned_by_item.get(key, 0) + a['weight_g']

    # Calculate needs from orders ONLY
    needs = {}  # product_id -> weight_needed_g

    for order in orders:
        for item in order['line_items']:
            item_name = item['name'].lower()
            # Determine weight per unit (250g or 500g)
            weight_per_unit = 500 if '500' in item['name'] else 250

            # Try to match product by NAME (strict matching)
            best_match = None
            best_match_score = 0

            for p in products:
                p_name = p['name'].lower().strip()

                # Exact name match in order item (highest priority)
                if p_name in item_name:
                    score = len(p_name) * 10  # Longer name matches are better
                    if score > best_match_score:
                        best_match = p
                        best_match_score = score

            if best_match:
                total_weight_needed = item['quantity'] * weight_per_unit
                # Subtract weight already assigned to this order item
                already_assigned = assigned_by_item.get((order['id'], item['id']), 0)
                weight_still_needed = max(0, total_weight_needed - already_assigned)

                if weight_still_needed > 0:
                    needs[best_match['id']] = needs.get(best_match['id'], 0) + weight_still_needed

    # Roast parameters: 888g green -> 750g roasted
    STANDARD_GREEN_WEIGHT_G = 888
    ROASTED_OUTPUT_G = 750  # approximately 15.5% weight loss

    suggestions = []

    # Only check products that have been ordered
    for product_id, needed_g in needs.items():
        packed_g = packed_by_product.get(product_id, 0)
        roasted_g = roasted_by_product.get(product_id, 0)
        total_available_g = packed_g + roasted_g

        if needed_g > total_available_g:
            shortfall_g = needed_g - total_available_g
            # Calculate how many 888g batches needed to cover shortfall
            batches_needed = math.ceil(shortfall_g / ROASTED_OUTPUT_G)

            product = products_by_id.get(product_id)
            if product:
                # Create SEPARATE suggestion for each batch (not combined)
                for batch_num in range(1, batches_needed + 1):
                    suggestions.append({
                        'product_id': product_id,
                        'product_name': product['name'],
                        'needed_g': needed_g,
                        'packed_g': int(packed_g),
                        'roasted_g': int(roasted_g),
                        'available_g': int(total_available_g),
                        'shortfall_g': shortfall_g,
                        'batch_number': batch_num,
                        'total_batches': batches_needed,
                        'suggested_roast_g': STANDARD_GREEN_WEIGHT_G,
                        'expected_output_g': ROASTED_OUTPUT_G,
                        'reason': 'order_need'
                    })

    return jsonify({
        'status': 'success',
        'suggestions': suggestions,
        'order_count': len(orders),
        'total_products_ordered': len(needs)
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


@roast_tracker.route('/api/adjust-batch-inventory', methods=['POST'])
@tracker_login_required
def api_adjust_batch_inventory():
    """Adjust inventory for a specific batch/LOT"""
    data = request.json
    batch_id = data.get('batch_id')
    adjustment_type = data.get('adjustment_type', 'set')  # add, subtract, set
    amount_g = data.get('amount_g')
    comment = data.get('comment', '').strip()

    if not batch_id or amount_g is None:
        return jsonify({'status': 'error', 'message': 'batch_id and amount_g are required'}), 400

    if not comment:
        return jsonify({'status': 'error', 'message': 'Comment is required for audit trail'}), 400

    if adjustment_type not in ('add', 'subtract', 'set'):
        return jsonify({'status': 'error', 'message': 'Invalid adjustment_type'}), 400

    conn = get_db()
    cur = conn.cursor()

    try:
        # Get batch info
        batch = cur.execute("""
            SELECT rb.id, rb.product_id, rb.available_weight_g, rb.lot_number
            FROM roast_batches rb
            WHERE rb.id = ?
        """, (batch_id,)).fetchone()

        if not batch:
            return jsonify({'status': 'error', 'message': 'Batch not found'}), 404

        previous_weight = batch['available_weight_g']

        # Calculate new weight
        if adjustment_type == 'add':
            new_weight = previous_weight + amount_g
        elif adjustment_type == 'subtract':
            new_weight = max(0, previous_weight - amount_g)
        else:  # set
            new_weight = amount_g

        # Update batch
        cur.execute("""
            UPDATE roast_batches SET available_weight_g = ? WHERE id = ?
        """, (new_weight, batch_id))

        # Get product total for audit trail
        result = cur.execute("""
            SELECT COALESCE(SUM(available_weight_g), 0) as total
            FROM roast_batches WHERE product_id = ?
        """, (batch['product_id'],)).fetchone()
        new_total = result['total']

        # Record the adjustment in audit log
        cur.execute("""
            INSERT INTO inventory_adjustments
            (product_id, batch_id, adjustment_type, amount_g, previous_total_g, new_total_g, comment)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (batch['product_id'], batch_id, adjustment_type, amount_g, previous_weight, new_weight,
              f"[LOT {batch['lot_number']}] {comment}"))

        conn.commit()

        return jsonify({
            'status': 'success',
            'previous_weight_g': previous_weight,
            'new_weight_g': new_weight,
            'lot_number': batch['lot_number']
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


@roast_tracker.route('/api/order-lot-assignments/<order_id>')
@tracker_login_required
def api_get_order_lot_assignments(order_id):
    """Get LOT assignments for an order (supports WC numeric IDs and B2B-xx IDs)"""
    assignments = query_db("""
        SELECT ola.*, pb.production_lot, pb.production_type, pb.package_size_g,
               rb.lot_number as source_lot, cp.name as product_name
        FROM order_lot_assignments ola
        JOIN production_batches pb ON ola.production_batch_id = pb.id
        LEFT JOIN roast_batches rb ON ola.roast_batch_id = rb.id
        LEFT JOIN coffee_products cp ON rb.product_id = cp.id
        WHERE ola.wc_order_id = ?
        ORDER BY ola.wc_order_item_id, ola.slot_number
    """, (str(order_id),))

    return jsonify({
        'status': 'success',
        'assignments': [dict(a) for a in assignments]
    })


@roast_tracker.route('/api/assign-lot', methods=['POST'])
@tracker_login_required
def api_assign_lot():
    """Assign a packed LOT to an order item slot and reduce inventory"""
    data = request.json
    order_id = data.get('order_id')
    order_item_id = data.get('order_item_id')
    slot_number = data.get('slot_number', 1)
    production_batch_id = data.get('production_batch_id')

    if not all([order_id, order_item_id, production_batch_id]):
        return jsonify({'status': 'error', 'message': 'order_id, order_item_id, and production_batch_id are required'}), 400

    conn = get_db()
    cur = conn.cursor()

    try:
        # Check if assignment already exists for this slot
        existing = cur.execute("""
            SELECT id, production_batch_id FROM order_lot_assignments
            WHERE wc_order_id = ? AND wc_order_item_id = ? AND slot_number = ?
        """, (order_id, order_item_id, slot_number)).fetchone()

        # Check production batch has enough stock (at least 1 unit)
        batch = cur.execute("""
            SELECT pb.id, pb.production_lot, pb.quantity, pb.package_size_g,
                   rb.id as roast_batch_id, rb.lot_number as source_lot
            FROM production_batches pb
            JOIN production_sources ps ON pb.id = ps.production_batch_id
            JOIN roast_batches rb ON ps.roast_batch_id = rb.id
            WHERE pb.id = ?
        """, (production_batch_id,)).fetchone()

        if not batch:
            return jsonify({'status': 'error', 'message': 'Production batch not found'}), 404

        if existing:
            # If changing to a different batch, restore stock to old batch first
            if existing['production_batch_id'] != production_batch_id:
                # Restore stock to old batch (add 1 unit back)
                cur.execute("""
                    UPDATE production_batches
                    SET quantity = quantity + 1
                    WHERE id = ?
                """, (existing['production_batch_id'],))

                # Check new batch has enough stock
                if batch['quantity'] < 1:
                    conn.rollback()
                    return jsonify({
                        'status': 'error',
                        'message': f'No stock available. Remaining: {batch["quantity"]} units'
                    }), 400

                # Deduct from new batch (remove 1 unit)
                cur.execute("""
                    UPDATE production_batches
                    SET quantity = quantity - 1
                    WHERE id = ?
                """, (production_batch_id,))

            # Update existing assignment
            cur.execute("""
                UPDATE order_lot_assignments
                SET production_batch_id = ?, roast_batch_id = ?, weight_g = ?, assigned_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (production_batch_id, batch['roast_batch_id'], batch['package_size_g'], existing['id']))
        else:
            # New assignment - check stock and deduct
            if batch['quantity'] < 1:
                return jsonify({
                    'status': 'error',
                    'message': f'No stock available. Remaining: {batch["quantity"]} units'
                }), 400

            # Deduct stock (remove 1 unit from production batch)
            cur.execute("""
                UPDATE production_batches
                SET quantity = quantity - 1
                WHERE id = ?
            """, (production_batch_id,))

            # Create new assignment
            cur.execute("""
                INSERT INTO order_lot_assignments
                (wc_order_id, wc_order_item_id, slot_number, production_batch_id, roast_batch_id, weight_g)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (order_id, order_item_id, slot_number, production_batch_id, batch['roast_batch_id'], batch['package_size_g']))

        conn.commit()

        # Get updated quantity for response
        updated_batch = cur.execute("""
            SELECT production_lot, quantity FROM production_batches WHERE id = ?
        """, (production_batch_id,)).fetchone()

        return jsonify({
            'status': 'success',
            'lot_number': batch['source_lot'],
            'production_lot': updated_batch['production_lot'] if updated_batch else None,
            'remaining_quantity': updated_batch['quantity'] if updated_batch else 0
        })

    except Exception as e:
        conn.rollback()
        return jsonify({'status': 'error', 'message': str(e)}), 500
    finally:
        conn.close()


@roast_tracker.route('/api/remove-lot-assignment', methods=['POST'])
@tracker_login_required
def api_remove_lot_assignment():
    """Remove a LOT assignment from an order item slot and restore stock"""
    data = request.json
    order_id = data.get('order_id')
    order_item_id = data.get('order_item_id')
    slot_number = data.get('slot_number', 1)

    if not all([order_id, order_item_id]):
        return jsonify({'status': 'error', 'message': 'order_id and order_item_id are required'}), 400

    conn = get_db()
    cur = conn.cursor()

    try:
        # Get the existing assignment to restore stock
        existing = cur.execute("""
            SELECT production_batch_id FROM order_lot_assignments
            WHERE wc_order_id = ? AND wc_order_item_id = ? AND slot_number = ?
        """, (order_id, order_item_id, slot_number)).fetchone()

        if existing:
            # Restore stock to the production batch (add 1 unit back)
            cur.execute("""
                UPDATE production_batches
                SET quantity = quantity + 1
                WHERE id = ?
            """, (existing['production_batch_id'],))

            # Delete the assignment
            cur.execute("""
                DELETE FROM order_lot_assignments
                WHERE wc_order_id = ? AND wc_order_item_id = ? AND slot_number = ?
            """, (order_id, order_item_id, slot_number))

            conn.commit()

        return jsonify({'status': 'success'})

    except Exception as e:
        conn.rollback()
        return jsonify({'status': 'error', 'message': str(e)}), 500
    finally:
        conn.close()


@roast_tracker.route('/api/available-lots')
@tracker_login_required
def api_available_lots():
    """Get all available LOTs for assignment (unpacked roasted coffee)"""
    lots = query_db("""
        SELECT rb.id, rb.lot_number, rb.available_weight_g, rb.roast_date,
               cp.id as product_id, cp.name as product_name, cp.roast_level, gc.country
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


@roast_tracker.route('/api/available-packed-lots')
@tracker_login_required
def api_available_packed_lots():
    """Get all available PACKED LOTs for order assignment (only packaged products can be shipped)"""
    lots = query_db("""
        SELECT pb.id as production_batch_id, pb.production_lot, pb.production_type,
               pb.package_size_g, pb.quantity as available_quantity,
               rb.id as roast_batch_id, rb.lot_number as source_lot, rb.roast_date,
               cp.id as product_id, cp.name as product_name, cp.roast_level, gc.country
        FROM production_batches pb
        JOIN production_sources ps ON pb.id = ps.production_batch_id
        JOIN roast_batches rb ON ps.roast_batch_id = rb.id
        JOIN coffee_products cp ON rb.product_id = cp.id
        LEFT JOIN green_coffee gc ON cp.green_coffee_id = gc.id
        WHERE pb.quantity > 0
          AND pb.production_type IN ('whole_bean_250', 'whole_bean_70', 'whole_bean_16', 'drip_11')
        ORDER BY cp.name, pb.package_size_g DESC, pb.production_date DESC
    """)

    return jsonify({
        'status': 'success',
        'lots': [dict(l) for l in lots]
    })


# ===== B2B Customer & Order Management =====

@roast_tracker.route('/b2b/customers')
@tracker_login_required
def b2b_customers():
    """List all B2B customers"""
    customers = query_db("""
        SELECT c.*,
               (SELECT COUNT(*) FROM b2b_orders WHERE customer_id = c.id) as order_count,
               (SELECT SUM(total) FROM b2b_orders WHERE customer_id = c.id AND payment_status = 'paid') as total_paid
        FROM b2b_customers c
        WHERE c.is_active = 1
        ORDER BY c.company_name
    """)
    return render_template('roast_tracker/b2b_customers.html', customers=customers)


@roast_tracker.route('/b2b/customers/new', methods=['GET', 'POST'])
@tracker_login_required
def b2b_customer_new():
    """Create new B2B customer"""
    if request.method == 'POST':
        company_name = request.form.get('company_name')
        contact_name = request.form.get('contact_name')
        email = request.form.get('email')
        phone = request.form.get('phone')
        vat_number = request.form.get('vat_number')
        address = request.form.get('address')
        city = request.form.get('city')
        postal_code = request.form.get('postal_code')
        country = request.form.get('country', 'HU')
        default_discount = request.form.get('default_discount_percent', type=float) or 0
        payment_terms = request.form.get('payment_terms_days', type=int) or 14
        notes = request.form.get('notes')

        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO b2b_customers (
                company_name, contact_name, email, phone, vat_number,
                address, city, postal_code, country,
                default_discount_percent, payment_terms_days, notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (company_name, contact_name, email, phone, vat_number,
              address, city, postal_code, country,
              default_discount, payment_terms, notes))
        customer_id = cur.lastrowid
        conn.commit()
        conn.close()

        flash(f'Customer "{company_name}" created successfully', 'success')
        return redirect(url_for('roast_tracker.b2b_customer_edit', customer_id=customer_id))

    return render_template('roast_tracker/b2b_customer_form.html', customer=None)


@roast_tracker.route('/b2b/customers/<int:customer_id>', methods=['GET', 'POST'])
@tracker_login_required
def b2b_customer_edit(customer_id):
    """View/edit B2B customer"""
    if request.method == 'POST':
        company_name = request.form.get('company_name')
        contact_name = request.form.get('contact_name')
        email = request.form.get('email')
        phone = request.form.get('phone')
        vat_number = request.form.get('vat_number')
        address = request.form.get('address')
        city = request.form.get('city')
        postal_code = request.form.get('postal_code')
        country = request.form.get('country', 'HU')
        default_discount = request.form.get('default_discount_percent', type=float) or 0
        payment_terms = request.form.get('payment_terms_days', type=int) or 14
        notes = request.form.get('notes')

        conn = get_db()
        conn.execute("""
            UPDATE b2b_customers SET
                company_name = ?, contact_name = ?, email = ?, phone = ?, vat_number = ?,
                address = ?, city = ?, postal_code = ?, country = ?,
                default_discount_percent = ?, payment_terms_days = ?, notes = ?
            WHERE id = ?
        """, (company_name, contact_name, email, phone, vat_number,
              address, city, postal_code, country,
              default_discount, payment_terms, notes, customer_id))
        conn.commit()
        conn.close()

        flash('Customer updated successfully', 'success')
        return redirect(url_for('roast_tracker.b2b_customer_edit', customer_id=customer_id))

    customer = query_db("SELECT * FROM b2b_customers WHERE id = ?", (customer_id,), one=True)
    if not customer:
        flash('Customer not found', 'error')
        return redirect(url_for('roast_tracker.b2b_customers'))

    # Get customer's product-specific discounts
    discounts = query_db("""
        SELECT d.*, p.name as product_name
        FROM b2b_customer_discounts d
        LEFT JOIN coffee_products p ON d.product_id = p.id
        WHERE d.customer_id = ?
    """, (customer_id,))

    # Get all products for discount dropdown
    products = query_db("SELECT id, name FROM coffee_products WHERE is_active = 1 AND COALESCE(is_archived, 0) = 0 ORDER BY name")

    # Get customer's orders
    orders = query_db("""
        SELECT * FROM b2b_orders
        WHERE customer_id = ?
        ORDER BY order_date DESC
        LIMIT 10
    """, (customer_id,))

    return render_template('roast_tracker/b2b_customer_form.html',
                           customer=customer, discounts=discounts,
                           products=products, orders=orders)


@roast_tracker.route('/b2b/customers/<int:customer_id>/discounts', methods=['POST'])
@tracker_login_required
def b2b_customer_discounts(customer_id):
    """Add/update product-specific discount for customer"""
    product_id = request.form.get('product_id', type=int)
    discount_percent = request.form.get('discount_percent', type=float)

    if not product_id or discount_percent is None:
        flash('Product and discount are required', 'error')
        return redirect(url_for('roast_tracker.b2b_customer_edit', customer_id=customer_id))

    conn = get_db()
    # Use REPLACE to insert or update
    conn.execute("""
        INSERT OR REPLACE INTO b2b_customer_discounts (customer_id, product_id, discount_percent)
        VALUES (?, ?, ?)
    """, (customer_id, product_id, discount_percent))
    conn.commit()
    conn.close()

    flash('Product discount saved', 'success')
    return redirect(url_for('roast_tracker.b2b_customer_edit', customer_id=customer_id))


@roast_tracker.route('/b2b/customers/<int:customer_id>/discounts/<int:discount_id>/delete', methods=['POST'])
@tracker_login_required
def b2b_customer_discount_delete(customer_id, discount_id):
    """Delete product-specific discount"""
    conn = get_db()
    conn.execute("DELETE FROM b2b_customer_discounts WHERE id = ? AND customer_id = ?",
                 (discount_id, customer_id))
    conn.commit()
    conn.close()
    flash('Product discount removed', 'success')
    return redirect(url_for('roast_tracker.b2b_customer_edit', customer_id=customer_id))


@roast_tracker.route('/b2b/orders')
@tracker_login_required
def b2b_orders():
    """List all B2B orders"""
    status_filter = request.args.get('status', '')
    payment_filter = request.args.get('payment', '')

    query = """
        SELECT o.*, c.company_name,
               (SELECT COALESCE(SUM(quantity), 0) FROM b2b_order_items WHERE order_id = o.id) as item_count
        FROM b2b_orders o
        JOIN b2b_customers c ON o.customer_id = c.id
        WHERE 1=1
    """
    params = []

    if status_filter:
        query += " AND o.status = ?"
        params.append(status_filter)
    if payment_filter:
        query += " AND o.payment_status = ?"
        params.append(payment_filter)

    query += " ORDER BY o.order_date DESC, o.id DESC"

    orders = query_db(query, params)
    customers = query_db("SELECT id, company_name FROM b2b_customers WHERE is_active = 1 ORDER BY company_name")

    return render_template('roast_tracker/b2b_orders.html',
                           orders=orders, customers=customers,
                           status_filter=status_filter, payment_filter=payment_filter,
                           today=date.today().isoformat())


@roast_tracker.route('/b2b/orders/new', methods=['GET', 'POST'])
@tracker_login_required
def b2b_order_new():
    """Create new B2B order"""
    if request.method == 'POST':
        customer_id = request.form.get('customer_id', type=int)
        order_date = request.form.get('order_date') or date.today().isoformat()
        notes = request.form.get('notes')

        if not customer_id:
            flash('Please select a customer', 'error')
            return redirect(url_for('roast_tracker.b2b_order_new'))

        # Get customer's payment terms for due date
        customer = query_db("SELECT payment_terms_days FROM b2b_customers WHERE id = ?",
                            (customer_id,), one=True)
        payment_terms = customer['payment_terms_days'] if customer else 14

        order_dt = datetime.strptime(order_date, '%Y-%m-%d')
        due_date = (order_dt + timedelta(days=payment_terms)).strftime('%Y-%m-%d')

        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO b2b_orders (customer_id, order_date, due_date, notes)
            VALUES (?, ?, ?, ?)
        """, (customer_id, order_date, due_date, notes))
        order_id = cur.lastrowid
        conn.commit()
        conn.close()

        flash('Order created. Add items to the order.', 'success')
        return redirect(url_for('roast_tracker.b2b_order_detail', order_id=order_id))

    customers = query_db("SELECT id, company_name FROM b2b_customers WHERE is_active = 1 ORDER BY company_name")
    return render_template('roast_tracker/b2b_order_form.html', customers=customers, today=date.today().isoformat())


@roast_tracker.route('/b2b/orders/<int:order_id>')
@tracker_login_required
def b2b_order_detail(order_id):
    """View B2B order detail"""
    order = query_db("""
        SELECT o.*, c.company_name, c.email, c.vat_number, c.default_discount_percent
        FROM b2b_orders o
        JOIN b2b_customers c ON o.customer_id = c.id
        WHERE o.id = ?
    """, (order_id,), one=True)

    if not order:
        flash('Order not found', 'error')
        return redirect(url_for('roast_tracker.b2b_orders'))

    items = query_db("SELECT * FROM b2b_order_items WHERE order_id = ? ORDER BY id", (order_id,))

    # Get available products from POS items table
    import sqlite3
    pos_db_path = '/home/brenesamerica/POS/pos_prod.db' if os.environ.get('BILLINGO_ENV') == 'prod' else '/home/brenesamerica/POS/pos_test.db'
    pos_conn = sqlite3.connect(pos_db_path)
    pos_conn.row_factory = sqlite3.Row
    pos_products = pos_conn.execute("""
        SELECT i.*, c.name as category_name
        FROM items i
        LEFT JOIN categories c ON i.category_id = c.id
        ORDER BY c.name, i.name
    """).fetchall()
    pos_conn.close()

    # Get coffee products from roast tracker (manually added coffees)
    roast_tracker_products = query_db("""
        SELECT
            cp.id,
            cp.name,
            gc.country as category_name,
            cp.roast_level,
            NULL as price
        FROM coffee_products cp
        LEFT JOIN green_coffee gc ON cp.green_coffee_id = gc.id
        WHERE cp.is_active = 1
        ORDER BY gc.country, cp.name
    """)

    # Combine products: POS items first, then roast tracker coffee products
    products = list(pos_products) + [dict(p) for p in roast_tracker_products]

    # Get customer's product discounts
    customer_discounts = query_db("""
        SELECT product_id, discount_percent
        FROM b2b_customer_discounts
        WHERE customer_id = ?
    """, (order['customer_id'],))
    discount_map = {d['product_id']: d['discount_percent'] for d in customer_discounts}

    # Get existing LOT assignments for this B2B order
    b2b_order_id = f"B2B-{order_id}"
    lot_assignments = query_db("""
        SELECT ola.*, pb.production_lot, rb.lot_number as source_lot,
               cp.name as product_name, pb.package_size_g
        FROM order_lot_assignments ola
        JOIN production_batches pb ON ola.production_batch_id = pb.id
        LEFT JOIN production_sources ps ON pb.id = ps.production_batch_id
        LEFT JOIN roast_batches rb ON ps.roast_batch_id = rb.id
        LEFT JOIN coffee_products cp ON rb.product_id = cp.id
        WHERE ola.wc_order_id = ?
        ORDER BY ola.wc_order_item_id, ola.slot_number
    """, (b2b_order_id,))

    # Group assignments by item_id
    assignments_by_item = {}
    for a in lot_assignments:
        item_id = a['wc_order_item_id']
        if item_id not in assignments_by_item:
            assignments_by_item[item_id] = []
        assignments_by_item[item_id].append(dict(a))

    # Get invoices for this order with payment status
    invoices = query_db("""
        SELECT DISTINCT billingo_document_id, MIN(invoiced_at) as invoiced_at, payment_status
        FROM b2b_item_invoices
        WHERE order_id = ?
        GROUP BY billingo_document_id
        ORDER BY invoiced_at DESC
    """, (order_id,))

    # Get invoice status for each item (how many have been invoiced)
    item_invoice_status = query_db("""
        SELECT order_item_id as item_id, SUM(quantity_invoiced) as invoiced
        FROM b2b_item_invoices
        WHERE order_id = ?
        GROUP BY order_item_id
    """, (order_id,))

    # Calculate order payment status based on invoices
    if invoices:
        paid_count = sum(1 for inv in invoices if inv['payment_status'] == 'paid')
        total_invoices = len(invoices)
        if paid_count == total_invoices:
            calculated_payment_status = 'paid'
        elif paid_count > 0:
            calculated_payment_status = 'partially_paid'
        else:
            calculated_payment_status = 'unpaid'
    else:
        calculated_payment_status = 'unpaid'

    return render_template('roast_tracker/b2b_order_detail.html',
                           order=order, items=items, products=products,
                           discount_map=discount_map,
                           assignments_by_item=assignments_by_item,
                           invoices=invoices,
                           item_invoice_status=item_invoice_status,
                           calculated_payment_status=calculated_payment_status)


@roast_tracker.route('/b2b/orders/<int:order_id>/items', methods=['POST'])
@tracker_login_required
def b2b_order_add_item(order_id):
    """Add item to B2B order"""
    product_id = request.form.get('product_id', type=int)
    product_name = request.form.get('product_name')
    quantity = request.form.get('quantity', type=int) or 1
    unit_price = request.form.get('unit_price', type=float)
    discount_percent = request.form.get('discount_percent', type=float) or 0
    package_size = request.form.get('package_size_g', type=int) or 250

    if not product_name or not unit_price:
        flash('Product name and price are required', 'error')
        return redirect(url_for('roast_tracker.b2b_order_detail', order_id=order_id))

    # Calculate line total after discount
    discounted_price = unit_price * (1 - discount_percent / 100)
    line_total = discounted_price * quantity

    conn = get_db()
    conn.execute("""
        INSERT INTO b2b_order_items (order_id, product_name, product_id, package_size_g,
                                     quantity, unit_price, discount_percent, line_total)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (order_id, product_name, product_id, package_size, quantity, unit_price, discount_percent, line_total))

    # Update order totals
    conn.execute("""
        UPDATE b2b_orders SET
            subtotal = (SELECT SUM(unit_price * quantity) FROM b2b_order_items WHERE order_id = ?),
            discount_total = (SELECT SUM(unit_price * quantity * discount_percent / 100) FROM b2b_order_items WHERE order_id = ?),
            total = (SELECT SUM(line_total) FROM b2b_order_items WHERE order_id = ?)
        WHERE id = ?
    """, (order_id, order_id, order_id, order_id))

    conn.commit()
    conn.close()

    flash(f'Added {quantity}x {product_name}', 'success')
    return redirect(url_for('roast_tracker.b2b_order_detail', order_id=order_id))


@roast_tracker.route('/b2b/orders/<int:order_id>/items/<int:item_id>/delete', methods=['POST'])
@tracker_login_required
def b2b_order_delete_item(order_id, item_id):
    """Remove item from B2B order"""
    conn = get_db()
    conn.execute("DELETE FROM b2b_order_items WHERE id = ? AND order_id = ?", (item_id, order_id))

    # Update order totals
    conn.execute("""
        UPDATE b2b_orders SET
            subtotal = COALESCE((SELECT SUM(unit_price * quantity) FROM b2b_order_items WHERE order_id = ?), 0),
            discount_total = COALESCE((SELECT SUM(unit_price * quantity * discount_percent / 100) FROM b2b_order_items WHERE order_id = ?), 0),
            total = COALESCE((SELECT SUM(line_total) FROM b2b_order_items WHERE order_id = ?), 0)
        WHERE id = ?
    """, (order_id, order_id, order_id, order_id))

    conn.commit()
    conn.close()

    flash('Item removed', 'success')
    return redirect(url_for('roast_tracker.b2b_order_detail', order_id=order_id))


@roast_tracker.route('/b2b/orders/<int:order_id>/items/<int:item_id>/edit', methods=['POST'])
@tracker_login_required
def b2b_order_edit_item(order_id, item_id):
    """Edit item in B2B order"""
    product_name = request.form.get('product_name')
    package_size_g = request.form.get('package_size_g', type=int)
    quantity = request.form.get('quantity', type=int)
    unit_price = request.form.get('unit_price', type=float)
    discount_percent = request.form.get('discount_percent', type=float, default=0)

    if not all([product_name, package_size_g, quantity, unit_price is not None]):
        flash('All fields are required', 'error')
        return redirect(url_for('roast_tracker.b2b_order_detail', order_id=order_id))

    line_total = unit_price * quantity * (1 - discount_percent / 100)

    conn = get_db()
    conn.execute("""
        UPDATE b2b_order_items SET
            product_name = ?,
            package_size_g = ?,
            quantity = ?,
            unit_price = ?,
            discount_percent = ?,
            line_total = ?
        WHERE id = ? AND order_id = ?
    """, (product_name, package_size_g, quantity, unit_price, discount_percent, line_total, item_id, order_id))

    # Update order totals
    conn.execute("""
        UPDATE b2b_orders SET
            subtotal = COALESCE((SELECT SUM(unit_price * quantity) FROM b2b_order_items WHERE order_id = ?), 0),
            discount_total = COALESCE((SELECT SUM(unit_price * quantity * discount_percent / 100) FROM b2b_order_items WHERE order_id = ?), 0),
            total = COALESCE((SELECT SUM(line_total) FROM b2b_order_items WHERE order_id = ?), 0)
        WHERE id = ?
    """, (order_id, order_id, order_id, order_id))

    conn.commit()
    conn.close()

    flash('Item updated', 'success')
    return redirect(url_for('roast_tracker.b2b_order_detail', order_id=order_id))


@roast_tracker.route('/b2b/orders/<int:order_id>/status', methods=['POST'])
@tracker_login_required
def b2b_order_status(order_id):
    """Update order status or payment status"""
    status = request.form.get('status')
    payment_status = request.form.get('payment_status')

    conn = get_db()
    if status:
        conn.execute("UPDATE b2b_orders SET status = ? WHERE id = ?", (status, order_id))
    if payment_status:
        conn.execute("UPDATE b2b_orders SET payment_status = ? WHERE id = ?", (payment_status, order_id))
    conn.commit()
    conn.close()

    flash('Order updated', 'success')
    return redirect(url_for('roast_tracker.b2b_order_detail', order_id=order_id))


@roast_tracker.route('/b2b/orders/<int:order_id>/invoice', methods=['POST'])
@tracker_login_required
def b2b_order_generate_invoice(order_id):
    """Generate Billingo invoice for all remaining (uninvoiced) items in B2B order"""
    import requests
    import json

    # Get Billingo settings from app config
    import sys
    sys.path.insert(0, '..')
    from app import BILLINGO_API_KEY, BILLINGO_BASE_URL, BILLINGO_INVOICE_BLOCK_ID

    # Get order with customer details
    order = query_db("""
        SELECT o.*, c.company_name, c.email, c.vat_number, c.address, c.city,
               c.postal_code, c.country, c.billingo_partner_id, c.payment_terms_days
        FROM b2b_orders o
        JOIN b2b_customers c ON o.customer_id = c.id
        WHERE o.id = ?
    """, (order_id,), one=True)

    if not order:
        flash('Order not found', 'error')
        return redirect(url_for('roast_tracker.b2b_orders'))

    # Get order items
    items = query_db("SELECT * FROM b2b_order_items WHERE order_id = ? ORDER BY id", (order_id,))

    if not items:
        flash('Cannot generate invoice for empty order', 'error')
        return redirect(url_for('roast_tracker.b2b_order_detail', order_id=order_id))

    # Get already invoiced quantities per item
    invoiced_items = query_db("""
        SELECT order_item_id, SUM(quantity_invoiced) as invoiced
        FROM b2b_item_invoices WHERE order_id = ?
        GROUP BY order_item_id
    """, (order_id,))
    invoiced_map = {i['order_item_id']: i['invoiced'] for i in invoiced_items}

    # Filter to only items with remaining quantity
    items_to_invoice = []
    for item in items:
        already_invoiced = invoiced_map.get(item['id'], 0)
        remaining = item['quantity'] - already_invoiced
        if remaining > 0:
            items_to_invoice.append((item, remaining))

    if not items_to_invoice:
        flash('All items have already been invoiced', 'info')
        return redirect(url_for('roast_tracker.b2b_order_detail', order_id=order_id))

    # Get payment method from form
    payment_method = request.form.get('payment_method', 'wire_transfer')

    # Get LOT assignments for this order
    lot_assignments = query_db("""
        SELECT ola.wc_order_item_id, pb.production_lot, rb.lot_number as source_lot
        FROM order_lot_assignments ola
        JOIN production_batches pb ON ola.production_batch_id = pb.id
        LEFT JOIN roast_batches rb ON ola.roast_batch_id = rb.id
        WHERE ola.wc_order_id = ?
        ORDER BY ola.wc_order_item_id, ola.slot_number
    """, (f"B2B-{order_id}",))

    # Group LOT numbers by item
    item_lots = {}
    for assignment in lot_assignments:
        item_id = assignment['wc_order_item_id']
        lot = assignment['source_lot'] or assignment['production_lot']
        if item_id not in item_lots:
            item_lots[item_id] = []
        item_lots[item_id].append(lot)

    # Check/create Billingo partner
    partner_id = order['billingo_partner_id']
    if not partner_id:
        partner_id = _create_billingo_partner(order, BILLINGO_API_KEY, BILLINGO_BASE_URL)
        if not partner_id:
            flash('Failed to create Billingo partner', 'error')
            return redirect(url_for('roast_tracker.b2b_order_detail', order_id=order_id))

    # Prepare invoice items with LOT numbers in comments
    invoice_items = []
    invoice_item_records = []  # For tracking in b2b_item_invoices

    for item, qty_to_invoice in items_to_invoice:
        # Get LOT numbers for this item (take the first N based on qty_to_invoice)
        lots = item_lots.get(item['id'], [])[:qty_to_invoice]
        lot_comment = f"LOT: {', '.join(lots)}" if lots else ""

        # Calculate net price with discount applied
        gross_price = item['unit_price']
        discount_percent = item['discount_percent'] or 0
        discounted_gross_price = gross_price * (1 - discount_percent / 100)
        net_price = discounted_gross_price / 1.27  # Remove 27% VAT

        invoice_items.append({
            "name": item['product_name'].strip(),
            "unit_price": round(net_price, 2),
            "unit_price_type": "net",
            "quantity": qty_to_invoice,
            "unit": "db",
            "vat": "27%",
            "comment": lot_comment
        })

        invoice_item_records.append({
            'item_id': item['id'],
            'quantity': qty_to_invoice,
            'lots': lots
        })

    # Create invoice
    document_id = _create_billingo_invoice(
        order, partner_id, payment_method, invoice_items,
        BILLINGO_API_KEY, BILLINGO_BASE_URL, BILLINGO_INVOICE_BLOCK_ID
    )

    if document_id:
        # Record invoiced items
        conn = get_db()
        for record in invoice_item_records:
            conn.execute("""
                INSERT INTO b2b_item_invoices (order_id, order_item_id, billingo_document_id, quantity_invoiced, lot_numbers)
                VALUES (?, ?, ?, ?, ?)
            """, (order_id, record['item_id'], document_id, record['quantity'], json.dumps(record['lots'])))

        # Also update legacy field for compatibility
        conn.execute("UPDATE b2b_orders SET billingo_document_id = ? WHERE id = ?", (document_id, order_id))
        conn.commit()
        conn.close()

        flash(f'Invoice #{document_id} generated successfully!', 'success')
    else:
        flash('Failed to create invoice', 'error')

    return redirect(url_for('roast_tracker.b2b_order_detail', order_id=order_id))


@roast_tracker.route('/b2b/orders/<int:order_id>/invoice/partial', methods=['POST'])
@tracker_login_required
def b2b_order_generate_partial_invoice(order_id):
    """Generate Billingo invoice for selected items in B2B order"""
    import requests
    import json

    import sys
    sys.path.insert(0, '..')
    from app import BILLINGO_API_KEY, BILLINGO_BASE_URL, BILLINGO_INVOICE_BLOCK_ID

    # Get order with customer details
    order = query_db("""
        SELECT o.*, c.company_name, c.email, c.vat_number, c.address, c.city,
               c.postal_code, c.country, c.billingo_partner_id, c.payment_terms_days
        FROM b2b_orders o
        JOIN b2b_customers c ON o.customer_id = c.id
        WHERE o.id = ?
    """, (order_id,), one=True)

    if not order:
        flash('Order not found', 'error')
        return redirect(url_for('roast_tracker.b2b_orders'))

    # Get selected item IDs and quantities
    item_ids = request.form.getlist('item_ids')
    if not item_ids:
        flash('No items selected for invoice', 'error')
        return redirect(url_for('roast_tracker.b2b_order_detail', order_id=order_id))

    payment_method = request.form.get('payment_method', 'wire_transfer')

    # Get LOT assignments for this order
    lot_assignments = query_db("""
        SELECT ola.wc_order_item_id, pb.production_lot, rb.lot_number as source_lot
        FROM order_lot_assignments ola
        JOIN production_batches pb ON ola.production_batch_id = pb.id
        LEFT JOIN roast_batches rb ON ola.roast_batch_id = rb.id
        WHERE ola.wc_order_id = ?
        ORDER BY ola.wc_order_item_id, ola.slot_number
    """, (f"B2B-{order_id}",))

    item_lots = {}
    for assignment in lot_assignments:
        item_id = assignment['wc_order_item_id']
        lot = assignment['source_lot'] or assignment['production_lot']
        if item_id not in item_lots:
            item_lots[item_id] = []
        item_lots[item_id].append(lot)

    # Check/create Billingo partner
    partner_id = order['billingo_partner_id']
    if not partner_id:
        partner_id = _create_billingo_partner(order, BILLINGO_API_KEY, BILLINGO_BASE_URL)
        if not partner_id:
            flash('Failed to create Billingo partner', 'error')
            return redirect(url_for('roast_tracker.b2b_order_detail', order_id=order_id))

    # Prepare invoice items
    invoice_items = []
    invoice_item_records = []

    for item_id_str in item_ids:
        item_id = int(item_id_str)
        item = query_db("SELECT * FROM b2b_order_items WHERE id = ?", (item_id,), one=True)
        if not item:
            continue

        # Get quantity to invoice from form
        qty_to_invoice = request.form.get(f'qty_{item_id}', type=int) or 1

        # Get selected LOTs from form (if provided)
        lots_json = request.form.get(f'lots_{item_id}', '')
        if lots_json:
            try:
                selected_lots = json.loads(lots_json)
            except:
                selected_lots = item_lots.get(item_id, [])[:qty_to_invoice]
        else:
            selected_lots = item_lots.get(item_id, [])[:qty_to_invoice]

        lot_comment = f"LOT: {', '.join(selected_lots)}" if selected_lots else ""

        # Calculate net price with discount applied
        gross_price = item['unit_price']
        discount_percent = item['discount_percent'] or 0
        discounted_gross_price = gross_price * (1 - discount_percent / 100)
        net_price = discounted_gross_price / 1.27  # Remove 27% VAT

        invoice_items.append({
            "name": item['product_name'].strip(),
            "unit_price": round(net_price, 2),
            "unit_price_type": "net",
            "quantity": qty_to_invoice,
            "unit": "db",
            "vat": "27%",
            "comment": lot_comment
        })

        invoice_item_records.append({
            'item_id': item_id,
            'quantity': qty_to_invoice,
            'lots': selected_lots
        })

    if not invoice_items:
        flash('No valid items to invoice', 'error')
        return redirect(url_for('roast_tracker.b2b_order_detail', order_id=order_id))

    # Create invoice
    document_id = _create_billingo_invoice(
        order, partner_id, payment_method, invoice_items,
        BILLINGO_API_KEY, BILLINGO_BASE_URL, BILLINGO_INVOICE_BLOCK_ID
    )

    if document_id:
        # Record invoiced items
        conn = get_db()
        for record in invoice_item_records:
            conn.execute("""
                INSERT INTO b2b_item_invoices (order_id, order_item_id, billingo_document_id, quantity_invoiced, lot_numbers)
                VALUES (?, ?, ?, ?, ?)
            """, (order_id, record['item_id'], document_id, record['quantity'], json.dumps(record['lots'])))
        conn.commit()
        conn.close()

        flash(f'Partial invoice #{document_id} generated successfully!', 'success')
    else:
        flash('Failed to create invoice', 'error')

    return redirect(url_for('roast_tracker.b2b_order_detail', order_id=order_id))


def _create_billingo_partner(order, api_key, base_url):
    """Helper to create Billingo partner"""
    import requests

    partner_payload = {
        "name": order['company_name'],
        "address": {
            "country_code": order['country'] or "HU",
            "post_code": order['postal_code'] or "",
            "city": order['city'] or "",
            "address": order['address'] or ""
        },
        "taxcode": order['vat_number'] or ""
    }
    if order['email']:
        partner_payload["emails"] = [order['email']]

    headers = {
        "X-API-KEY": api_key,
        "Content-Type": "application/json"
    }

    response = requests.post(f"{base_url}/partners", json=partner_payload, headers=headers)

    if response.status_code == 201:
        partner_data = response.json()
        partner_id = partner_data['id']
        # Save partner ID to customer
        conn = get_db()
        conn.execute("UPDATE b2b_customers SET billingo_partner_id = ? WHERE id = ?",
                    (partner_id, order['customer_id']))
        conn.commit()
        conn.close()
        return partner_id
    return None


def _create_billingo_invoice(order, partner_id, payment_method, items, api_key, base_url, block_id):
    """Helper to create Billingo invoice"""
    import requests
    from datetime import date

    # For consignment orders, use today's date for both fulfillment and due date
    if order['status'] == 'consignment':
        today = date.today().isoformat()
        fulfillment_date = today
        due_date = today
    else:
        fulfillment_date = order['order_date']
        due_date = order['due_date']

    invoice_payload = {
        "partner_id": partner_id,
        "block_id": block_id,
        "type": "invoice",
        "fulfillment_date": fulfillment_date,
        "due_date": due_date,
        "payment_method": payment_method,
        "language": "hu",
        "currency": "HUF",
        "conversion_rate": 1,
        "electronic": True,
        "paid": order['payment_status'] == 'paid',
        "items": items,
        "comment": order['notes'] or ""
    }

    headers = {
        "X-API-KEY": api_key,
        "Content-Type": "application/json"
    }

    response = requests.post(f"{base_url}/documents", json=invoice_payload, headers=headers)

    if response.status_code == 201:
        return response.json()['id']
    return None


@roast_tracker.route('/b2b/invoice/<int:document_id>/download')
@tracker_login_required
def b2b_download_invoice_by_id(document_id):
    """Download invoice PDF by Billingo document ID"""
    import requests
    import io
    from flask import send_file

    import sys
    sys.path.insert(0, '..')
    from app import BILLINGO_API_KEY, BILLINGO_BASE_URL

    headers = {"X-API-KEY": BILLINGO_API_KEY}

    response = requests.get(f"{BILLINGO_BASE_URL}/documents/{document_id}/download", headers=headers)

    if response.status_code == 200:
        pdf_stream = io.BytesIO(response.content)
        return send_file(
            pdf_stream,
            as_attachment=True,
            download_name=f"invoice_{document_id}.pdf",
            mimetype="application/pdf"
        )
    else:
        flash('Failed to download invoice', 'error')
        return redirect(request.referrer or url_for('roast_tracker.b2b_orders'))


@roast_tracker.route('/b2b/orders/<int:order_id>/invoice/<int:document_id>/cancel', methods=['POST'])
@tracker_login_required
def b2b_cancel_invoice_by_id(order_id, document_id):
    """Cancel/Sztornó a specific invoice by document ID"""
    import requests

    import sys
    sys.path.insert(0, '..')
    from app import BILLINGO_API_KEY, BILLINGO_BASE_URL

    cancellation_reason = request.form.get('cancellation_reason', 'Cancelled')

    headers = {
        "X-API-KEY": BILLINGO_API_KEY,
        "Content-Type": "application/json"
    }

    # Create cancellation document in Billingo
    response = requests.post(
        f"{BILLINGO_BASE_URL}/documents/{document_id}/cancel",
        headers=headers,
        json={"cancellation_reason": cancellation_reason}
    )

    if response.status_code in [200, 201]:
        # Remove the invoice records from our tracking table
        conn = get_db()
        conn.execute("DELETE FROM b2b_item_invoices WHERE order_id = ? AND billingo_document_id = ?",
                    (order_id, document_id))

        # If this was the main order invoice, clear that too
        conn.execute("UPDATE b2b_orders SET billingo_document_id = NULL WHERE id = ? AND billingo_document_id = ?",
                    (order_id, document_id))
        conn.commit()
        conn.close()

        flash(f'Invoice #{document_id} cancelled successfully (Sztornó created)', 'success')
    else:
        flash(f'Failed to cancel invoice: {response.text}', 'error')

    return redirect(url_for('roast_tracker.b2b_order_detail', order_id=order_id))


@roast_tracker.route('/b2b/orders/<int:order_id>/invoice/<int:document_id>/payment', methods=['POST'])
@tracker_login_required
def b2b_invoice_payment_status(order_id, document_id):
    """Update payment status for a specific invoice"""
    payment_status = request.form.get('payment_status', 'unpaid')

    conn = get_db()
    conn.execute("""
        UPDATE b2b_item_invoices
        SET payment_status = ?
        WHERE order_id = ? AND billingo_document_id = ?
    """, (payment_status, order_id, document_id))
    conn.commit()
    conn.close()

    flash(f'Invoice #{document_id} marked as {payment_status}', 'success')
    return redirect(url_for('roast_tracker.b2b_order_detail', order_id=order_id))


@roast_tracker.route('/b2b/orders/<int:order_id>/invoice/download')
@tracker_login_required
def b2b_order_download_invoice(order_id):
    """Download invoice PDF for B2B order"""
    import requests
    import io
    from flask import send_file

    import sys
    sys.path.insert(0, '..')
    from app import BILLINGO_API_KEY, BILLINGO_BASE_URL

    order = query_db("SELECT billingo_document_id FROM b2b_orders WHERE id = ?", (order_id,), one=True)

    if not order or not order['billingo_document_id']:
        flash('No invoice found for this order', 'error')
        return redirect(url_for('roast_tracker.b2b_order_detail', order_id=order_id))

    document_id = order['billingo_document_id']

    headers = {
        "X-API-KEY": BILLINGO_API_KEY
    }

    response = requests.get(
        f"{BILLINGO_BASE_URL}/documents/{document_id}/download",
        headers=headers
    )

    if response.status_code == 200:
        pdf_stream = io.BytesIO(response.content)
        return send_file(
            pdf_stream,
            as_attachment=True,
            download_name=f"invoice_B2B_{order_id}.pdf",
            mimetype="application/pdf"
        )
    else:
        flash(f'Failed to download invoice: {response.text}', 'error')
        return redirect(url_for('roast_tracker.b2b_order_detail', order_id=order_id))


@roast_tracker.route('/b2b/orders/<int:order_id>/invoice/cancel', methods=['POST'])
@tracker_login_required
def b2b_order_cancel_invoice(order_id):
    """Cancel/Sztornó invoice for B2B order"""
    import requests

    import sys
    sys.path.insert(0, '..')
    from app import BILLINGO_API_KEY, BILLINGO_BASE_URL

    cancellation_reason = request.form.get('cancellation_reason', 'Sztornó')

    order = query_db("SELECT billingo_document_id FROM b2b_orders WHERE id = ?", (order_id,), one=True)

    if not order or not order['billingo_document_id']:
        flash('No invoice found for this order', 'error')
        return redirect(url_for('roast_tracker.b2b_order_detail', order_id=order_id))

    document_id = order['billingo_document_id']

    headers = {
        "X-API-KEY": BILLINGO_API_KEY,
        "Content-Type": "application/json"
    }

    # Call Billingo API to cancel the document
    response = requests.post(
        f"{BILLINGO_BASE_URL}/documents/{document_id}/cancel",
        headers=headers,
        json={"cancellation_reason": cancellation_reason}
    )

    if response.status_code in [200, 201]:
        # Clear the document ID from order so a new invoice can be generated
        conn = get_db()
        conn.execute("UPDATE b2b_orders SET billingo_document_id = NULL WHERE id = ?", (order_id,))
        conn.commit()
        conn.close()

        flash(f'Invoice cancelled successfully. Reason: {cancellation_reason}', 'success')
    else:
        flash(f'Failed to cancel invoice: {response.text}', 'error')

    return redirect(url_for('roast_tracker.b2b_order_detail', order_id=order_id))


# ============ WooCommerce Invoice Routes ============

@roast_tracker.route('/wc/orders/<int:order_id>/invoice', methods=['POST'])
@tracker_login_required
def wc_generate_invoice(order_id):
    """Generate Billingo invoice for WooCommerce order"""
    import requests
    from datetime import date

    import sys
    sys.path.insert(0, '..')
    from app import BILLINGO_API_KEY, BILLINGO_BASE_URL, BILLINGO_INVOICE_BLOCK_ID, fetch_wc_orders

    # Check if invoice already exists
    existing = query_db("SELECT billingo_document_id FROM wc_order_invoices WHERE wc_order_id = ?",
                        (order_id,), one=True)
    if existing:
        flash(f'Invoice already exists: #{existing["billingo_document_id"]}', 'info')
        return redirect(url_for('roast_tracker.orders'))

    # Fetch the order from WooCommerce
    orders = fetch_wc_orders(status='processing')
    order = next((o for o in orders if o['id'] == order_id), None)

    if not order:
        # Try completed orders too
        orders = fetch_wc_orders(status='completed')
        order = next((o for o in orders if o['id'] == order_id), None)

    if not order:
        flash('Order not found in WooCommerce', 'error')
        return redirect(url_for('roast_tracker.orders'))

    billing = order['billing']
    payment_method = request.form.get('payment_method', 'wire_transfer')

    # Create or find Billingo partner
    headers = {
        "X-API-KEY": BILLINGO_API_KEY,
        "Content-Type": "application/json"
    }

    # Build partner name
    partner_name = f"{billing['first_name']} {billing['last_name']}".strip()
    if billing.get('company'):
        partner_name = billing['company']

    partner_payload = {
        "name": partner_name,
        "address": {
            "country_code": billing.get('country', 'HU'),
            "post_code": billing.get('postcode', ''),
            "city": billing.get('city', ''),
            "address": billing.get('address_1', '')
        }
    }
    if billing.get('email'):
        partner_payload["emails"] = [billing['email']]
    if billing.get('phone'):
        partner_payload["phone"] = billing['phone']

    partner_response = requests.post(
        f"{BILLINGO_BASE_URL}/partners",
        json=partner_payload,
        headers=headers
    )

    if partner_response.status_code != 201:
        flash(f'Failed to create Billingo partner: {partner_response.text}', 'error')
        return redirect(url_for('roast_tracker.orders'))

    partner_id = partner_response.json()['id']

    # Get LOT assignments for invoice comment
    lot_assignments = query_db("""
        SELECT ola.wc_order_item_id, pb.production_lot, rb.lot_number as source_lot
        FROM order_lot_assignments ola
        JOIN production_batches pb ON ola.production_batch_id = pb.id
        LEFT JOIN roast_batches rb ON ola.roast_batch_id = rb.id
        WHERE ola.wc_order_id = ?
        ORDER BY ola.wc_order_item_id, ola.slot_number
    """, (order_id,))

    item_lots = {}
    for assignment in lot_assignments:
        item_id = assignment['wc_order_item_id']
        lot = assignment['source_lot'] or assignment['production_lot']
        if item_id not in item_lots:
            item_lots[item_id] = []
        item_lots[item_id].append(lot)

    # Prepare invoice items
    invoice_items = []
    for item in order['line_items']:
        lots = item_lots.get(item['id'], [])
        lot_comment = f"LOT: {', '.join(lots)}" if lots else ""

        # Calculate net price from subtotal (which is already net in WC)
        subtotal = float(item.get('subtotal', 0))
        quantity = item['quantity']
        net_price = subtotal / quantity if quantity > 0 else 0

        invoice_items.append({
            "name": item['name'].strip(),
            "unit_price": round(net_price, 2),
            "unit_price_type": "net",
            "quantity": quantity,
            "unit": "db",
            "vat": "27%",
            "comment": lot_comment
        })

    # Create invoice
    today = date.today().isoformat()
    invoice_payload = {
        "partner_id": partner_id,
        "block_id": BILLINGO_INVOICE_BLOCK_ID,
        "type": "invoice",
        "fulfillment_date": today,
        "due_date": today,
        "payment_method": payment_method,
        "language": "hu",
        "currency": order.get('currency', 'HUF'),
        "conversion_rate": 1,
        "electronic": True,
        "paid": True,  # WooCommerce orders are typically already paid
        "items": invoice_items
    }

    response = requests.post(
        f"{BILLINGO_BASE_URL}/documents",
        json=invoice_payload,
        headers=headers
    )

    if response.status_code == 201:
        document_id = response.json()['id']

        # Save to database
        conn = get_db()
        conn.execute("""
            INSERT INTO wc_order_invoices (wc_order_id, billingo_document_id, billingo_partner_id)
            VALUES (?, ?, ?)
        """, (order_id, document_id, partner_id))
        conn.commit()
        conn.close()

        flash(f'Invoice #{document_id} created successfully!', 'success')
    else:
        flash(f'Failed to create invoice: {response.text}', 'error')

    return redirect(url_for('roast_tracker.orders'))


@roast_tracker.route('/wc/orders/<int:order_id>/invoice/download')
@tracker_login_required
def wc_download_invoice(order_id):
    """Download invoice PDF for WooCommerce order"""
    import requests
    import io
    from flask import send_file

    import sys
    sys.path.insert(0, '..')
    from app import BILLINGO_API_KEY, BILLINGO_BASE_URL

    invoice = query_db("SELECT billingo_document_id FROM wc_order_invoices WHERE wc_order_id = ?",
                       (order_id,), one=True)

    if not invoice:
        flash('No invoice found for this order', 'error')
        return redirect(url_for('roast_tracker.orders'))

    document_id = invoice['billingo_document_id']
    headers = {"X-API-KEY": BILLINGO_API_KEY}

    response = requests.get(f"{BILLINGO_BASE_URL}/documents/{document_id}/download", headers=headers)

    if response.status_code == 200:
        pdf_stream = io.BytesIO(response.content)
        return send_file(
            pdf_stream,
            as_attachment=True,
            download_name=f"invoice_WC_{order_id}.pdf",
            mimetype="application/pdf"
        )
    else:
        flash('Failed to download invoice', 'error')
        return redirect(url_for('roast_tracker.orders'))


@roast_tracker.route('/api/b2b/customers')
@tracker_login_required
def api_b2b_customers():
    """Get B2B customers for autocomplete"""
    customers = query_db("""
        SELECT id, company_name, email, default_discount_percent, payment_terms_days
        FROM b2b_customers
        WHERE is_active = 1
        ORDER BY company_name
    """)
    return jsonify({'status': 'success', 'customers': [dict(c) for c in customers]})


@roast_tracker.route('/api/b2b/customer/<int:customer_id>/discounts')
@tracker_login_required
def api_b2b_customer_discounts(customer_id):
    """Get customer's discount structure"""
    customer = query_db("SELECT default_discount_percent FROM b2b_customers WHERE id = ?",
                        (customer_id,), one=True)
    discounts = query_db("""
        SELECT product_id, discount_percent
        FROM b2b_customer_discounts
        WHERE customer_id = ?
    """, (customer_id,))

    return jsonify({
        'status': 'success',
        'default_discount': customer['default_discount_percent'] if customer else 0,
        'product_discounts': {d['product_id']: d['discount_percent'] for d in discounts}
    })


# Initialize database on first request
@roast_tracker.before_app_request
def ensure_db():
    """Ensure database exists"""
    from .database import DATABASE_PATH
    import os
    if not os.path.exists(DATABASE_PATH):
        init_db()
