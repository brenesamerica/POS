"""
LOT Number Generator for Café Tiko Roast Tracker

LOT Number Formats:
- Roasted Coffee: X/YEARMONTHDAY/Y (e.g., V/2025NOV05/1)
- Drip Coffee: TG/X/YEARMONTHDAY/Y
- Advent Calendar: AK/YEARMONTHDAY/Y
- Cold Brew: CB/YEARMONTHDAY/Y
"""
from datetime import datetime, date
from .database import query_db


# Hungarian month abbreviations
MONTH_CODES = {
    1: 'JAN',
    2: 'FEB',
    3: 'MÁR',
    4: 'ÁPR',
    5: 'MÁJ',
    6: 'JÚN',
    7: 'JÚL',
    8: 'AUG',
    9: 'SZEPT',
    10: 'OKT',
    11: 'NOV',
    12: 'DEC'
}

# Reverse lookup for parsing
MONTH_NUMBERS = {v: k for k, v in MONTH_CODES.items()}

# Roast level codes
ROAST_LEVELS = {
    'V': 'Világos (Light)',
    'K': 'Közepes (Medium)',
    'S': 'Sötét (Dark)'
}


def format_date_part(d: date) -> str:
    """
    Format date as YEARMONTHDAY for LOT number
    Example: 2025NOV05
    """
    year = d.year
    month = MONTH_CODES[d.month]
    day = f"{d.day:02d}"
    return f"{year}{month}{day}"


def parse_date_from_lot(date_part: str) -> date:
    """
    Parse YEARMONTHDAY from LOT number back to date
    Example: 2025NOV05 -> date(2025, 11, 5)
    """
    year = int(date_part[:4])

    # Find month (variable length due to SZEPT)
    for month_code, month_num in MONTH_NUMBERS.items():
        if date_part[4:].startswith(month_code):
            month = month_num
            day = int(date_part[4 + len(month_code):])
            return date(year, month, day)

    raise ValueError(f"Could not parse date from: {date_part}")


def get_next_sequence(roast_level: str, roast_date: date) -> int:
    """
    Get the next sequence number for a roast level on a given date.

    Args:
        roast_level: V, K, or S
        roast_date: Date of the roast

    Returns:
        Next sequence number (1-based)
    """
    date_part = format_date_part(roast_date)
    prefix = f"{roast_level}/{date_part}/"

    # Find existing LOTs for this level and date
    existing = query_db(
        "SELECT lot_number FROM roast_batches WHERE lot_number LIKE ?",
        (f"{prefix}%",)
    )

    if not existing:
        return 1

    # Get max sequence
    max_seq = 0
    for row in existing:
        lot = row['lot_number']
        seq = int(lot.split('/')[-1])
        max_seq = max(max_seq, seq)

    return max_seq + 1


def generate_roast_lot(roast_level: str, roast_date: date, product_id: int = None, custom_sequence: int = None) -> str:
    """
    Generate a LOT number for a roast batch.

    Rule: If same roast level + same product on same day, return existing LOT

    Args:
        roast_level: V (light), K (medium), or S (dark)
        roast_date: Date of the roast
        product_id: Optional product ID to check for existing same-product roast
        custom_sequence: Optional custom sequence number (for retroactive entries)

    Returns:
        LOT number string (e.g., "V/2025NOV05/1")
    """
    if roast_level not in ROAST_LEVELS:
        raise ValueError(f"Invalid roast level: {roast_level}. Must be V, K, or S")

    date_part = format_date_part(roast_date)

    # Check if same product already roasted same day with same level
    if product_id:
        existing = query_db(
            """SELECT lot_number FROM roast_batches
               WHERE product_id = ? AND roast_level = ? AND roast_date = ?""",
            (product_id, roast_level, roast_date.isoformat())
        )
        if existing:
            return existing[0]['lot_number']

    # Use custom sequence if provided, otherwise generate next
    if custom_sequence is not None:
        seq = custom_sequence
    else:
        seq = get_next_sequence(roast_level, roast_date)
    return f"{roast_level}/{date_part}/{seq}"


def generate_drip_lot(roast_level: str, production_date: date) -> str:
    """
    Generate a LOT number for drip coffee production.
    Format: TG/X/YEARMONTHDAY/Y

    Args:
        roast_level: V or K (drip usually from light or medium)
        production_date: Date of production

    Returns:
        LOT number string (e.g., "TG/V/2025NOV05/1")
    """
    date_part = format_date_part(production_date)
    prefix = f"TG/{roast_level}/{date_part}/"

    # Find existing for today
    existing = query_db(
        "SELECT production_lot FROM production_batches WHERE production_lot LIKE ?",
        (f"{prefix}%",)
    )

    seq = len(existing) + 1 if existing else 1
    return f"TG/{roast_level}/{date_part}/{seq}"


def generate_advent_lot(production_date: date) -> str:
    """
    Generate a LOT number for advent calendar.
    Format: AK/YEARMONTHDAY/Y

    Args:
        production_date: Date of production

    Returns:
        LOT number string (e.g., "AK/2025NOV05/1")
    """
    date_part = format_date_part(production_date)
    prefix = f"AK/{date_part}/"

    # Find existing for today
    existing = query_db(
        "SELECT DISTINCT advent_lot FROM advent_calendar_contents WHERE advent_lot LIKE ?",
        (f"{prefix}%",)
    )

    seq = len(existing) + 1 if existing else 1
    return f"AK/{date_part}/{seq}"


def generate_cold_brew_lot(production_date: date) -> str:
    """
    Generate a LOT number for cold brew.
    Format: CB/YEARMONTHDAY/Y (usually Y=1)

    Args:
        production_date: Date of production

    Returns:
        LOT number string (e.g., "CB/2025NOV05/1")
    """
    date_part = format_date_part(production_date)
    prefix = f"CB/{date_part}/"

    # Find existing for today
    existing = query_db(
        "SELECT production_lot FROM production_batches WHERE production_lot LIKE ?",
        (f"{prefix}%",)
    )

    seq = len(existing) + 1 if existing else 1
    return f"CB/{date_part}/{seq}"


def parse_lot_number(lot: str) -> dict:
    """
    Parse a LOT number and extract its components.

    Returns:
        dict with type, roast_level (if applicable), date, sequence
    """
    parts = lot.split('/')

    if parts[0] == 'TG':
        # Drip: TG/X/YEARMONTHDAY/Y
        return {
            'type': 'drip',
            'roast_level': parts[1],
            'date': parse_date_from_lot(parts[2]),
            'sequence': int(parts[3])
        }
    elif parts[0] == 'AK':
        # Advent: AK/YEARMONTHDAY/Y
        return {
            'type': 'advent',
            'date': parse_date_from_lot(parts[1]),
            'sequence': int(parts[2])
        }
    elif parts[0] == 'CB':
        # Cold Brew: CB/YEARMONTHDAY/Y
        return {
            'type': 'cold_brew',
            'date': parse_date_from_lot(parts[1]),
            'sequence': int(parts[2])
        }
    elif parts[0] in ROAST_LEVELS:
        # Roast: X/YEARMONTHDAY/Y
        return {
            'type': 'roast',
            'roast_level': parts[0],
            'date': parse_date_from_lot(parts[1]),
            'sequence': int(parts[2])
        }
    else:
        raise ValueError(f"Unknown LOT format: {lot}")


# Convenience function to get roast level name
def get_roast_level_name(code: str) -> str:
    """Get human-readable name for roast level code"""
    return ROAST_LEVELS.get(code, code)
