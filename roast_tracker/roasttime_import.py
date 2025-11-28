"""
RoastTime Data Import for Cafe Tiko Roast Tracker

Imports roast data from Aillio Bullet RoastTime application.
Default location: C:/Users/{user}/AppData/Roaming/roast-time/roasts/
"""
import json
import os
from datetime import datetime
from typing import Optional, List, Dict, Any


# Default RoastTime data location
DEFAULT_ROASTTIME_PATH = os.path.expanduser(
    "~/AppData/Roaming/roast-time/roasts"
)


def get_roasttime_path() -> str:
    """Get the RoastTime roasts directory path"""
    # Try Windows path
    win_path = os.path.expandvars(r"%APPDATA%\roast-time\roasts")
    if os.path.exists(win_path):
        return win_path

    # Try Linux/WSL path
    wsl_path = "/mnt/c/Users/brene/AppData/Roaming/roast-time/roasts"
    if os.path.exists(wsl_path):
        return wsl_path

    return DEFAULT_ROASTTIME_PATH


def load_roast_file(filepath: str) -> Optional[Dict[str, Any]]:
    """Load a single RoastTime JSON file"""
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f"Error loading {filepath}: {e}")
        return None


def parse_roast_data(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Parse RoastTime data into our format.

    Returns dict with:
        - roasttime_uid: Original file UID
        - roast_name: Name from RoastTime
        - roast_date: Date of roast
        - green_weight_g: Green coffee weight
        - roasted_weight_g: Roasted coffee weight
        - weight_loss_percent: Calculated weight loss
        - preheat_temp: Preheat temperature
        - charge_temp: Bean charge temperature
        - drop_temp: Bean drop temperature
        - first_crack_time: Time to first crack (seconds)
        - first_crack_temp: Temperature at first crack
        - total_roast_time: Total roast time (seconds)
        - ambient_temp: Ambient temperature
        - humidity: Humidity percentage
        - roast_number: Bullet roast counter
        - bean_temps: List of bean temperatures (for graphing)
        - drum_temps: List of drum temperatures
        - ror: Rate of Rise data
    """
    # Basic info
    uid = data.get('uid', '')
    roast_name = data.get('roastName', 'Unknown')

    # Date
    timestamp_ms = data.get('dateTime', 0)
    roast_date = datetime.fromtimestamp(timestamp_ms / 1000) if timestamp_ms else None

    # Weights (may be string or numeric)
    green_weight = float(data.get('weightGreen', 0) or 0)
    roasted_weight = float(data.get('weightRoasted', 0) or 0)
    weight_loss = ((green_weight - roasted_weight) / green_weight * 100) if green_weight > 0 else 0

    # Temperatures
    preheat_temp = data.get('preheatTemperature', 0)
    charge_temp = data.get('beanChargeTemperature', 0)
    drop_temp = data.get('beanDropTemperature', 0)
    drum_charge_temp = data.get('drumChargeTemperature', 0)
    drum_drop_temp = data.get('drumDropTemperature', 0)

    # Time data
    sample_rate = data.get('sampleRate', 2)  # Usually 2 = 0.5 second intervals
    total_roast_time = data.get('totalRoastTime', 0)

    # First crack
    fc_index = data.get('indexFirstCrackStart', 0)
    fc_time = fc_index / sample_rate if fc_index > 0 else None

    # Get FC temperature from bean temps at that index
    bean_temps = data.get('beanTemperature', [])
    fc_temp = bean_temps[fc_index] if fc_index > 0 and fc_index < len(bean_temps) else None

    # Environment
    ambient = data.get('ambient', 0)
    humidity = data.get('humidity', 0)

    # Roast number (Bullet counter)
    roast_number = data.get('roastNumber', 0)

    # Time series data
    drum_temps = data.get('drumTemperature', [])
    ror = data.get('beanDerivative', [])  # Rate of Rise

    return {
        'roasttime_uid': uid,
        'roast_name': roast_name,
        'roast_date': roast_date,
        'green_weight_g': green_weight,
        'roasted_weight_g': roasted_weight,
        'weight_loss_percent': round(weight_loss, 2),
        'preheat_temp': preheat_temp,
        'charge_temp': charge_temp,
        'drop_temp': drop_temp,
        'drum_charge_temp': drum_charge_temp,
        'drum_drop_temp': drum_drop_temp,
        'first_crack_time': int(fc_time) if fc_time else None,
        'first_crack_temp': round(fc_temp, 1) if fc_temp else None,
        'total_roast_time': total_roast_time,
        'ambient_temp': ambient,
        'humidity': humidity,
        'roast_number': roast_number,
        'sample_rate': sample_rate,
        'bean_temps': bean_temps,
        'drum_temps': drum_temps,
        'ror': ror,
    }


def list_roast_files(path: str = None) -> List[str]:
    """List all roast files in the RoastTime directory"""
    if path is None:
        path = get_roasttime_path()

    if not os.path.exists(path):
        return []

    files = []
    for f in os.listdir(path):
        filepath = os.path.join(path, f)
        if os.path.isfile(filepath):
            files.append(filepath)

    return files


def load_all_roasts(path: str = None) -> List[Dict[str, Any]]:
    """Load and parse all roasts from RoastTime"""
    files = list_roast_files(path)
    roasts = []

    for filepath in files:
        data = load_roast_file(filepath)
        if data:
            parsed = parse_roast_data(data)
            roasts.append(parsed)

    # Sort by date, newest first
    roasts.sort(key=lambda x: x['roast_date'] or datetime.min, reverse=True)
    return roasts


def get_roast_by_uid(uid: str, path: str = None) -> Optional[Dict[str, Any]]:
    """Load a specific roast by its UID"""
    if path is None:
        path = get_roasttime_path()

    filepath = os.path.join(path, uid)
    if os.path.exists(filepath):
        data = load_roast_file(filepath)
        if data:
            return parse_roast_data(data)

    return None


def search_roasts_by_name(search_term: str, path: str = None) -> List[Dict[str, Any]]:
    """Search roasts by name"""
    all_roasts = load_all_roasts(path)
    search_lower = search_term.lower()
    return [r for r in all_roasts if search_lower in r['roast_name'].lower()]


def get_roasts_by_date_range(start_date: datetime, end_date: datetime, path: str = None) -> List[Dict[str, Any]]:
    """Get roasts within a date range"""
    all_roasts = load_all_roasts(path)
    return [
        r for r in all_roasts
        if r['roast_date'] and start_date <= r['roast_date'] <= end_date
    ]


def get_roast_summary(path: str = None) -> Dict[str, Any]:
    """Get summary statistics of all roasts"""
    roasts = load_all_roasts(path)

    if not roasts:
        return {'count': 0}

    total_green = sum(r['green_weight_g'] for r in roasts)
    total_roasted = sum(r['roasted_weight_g'] for r in roasts)

    return {
        'count': len(roasts),
        'total_green_kg': round(total_green / 1000, 2),
        'total_roasted_kg': round(total_roasted / 1000, 2),
        'avg_weight_loss': round(sum(r['weight_loss_percent'] for r in roasts) / len(roasts), 2),
        'earliest': min(r['roast_date'] for r in roasts if r['roast_date']),
        'latest': max(r['roast_date'] for r in roasts if r['roast_date']),
    }


# Guess roast level from name or characteristics
def guess_roast_level(roast_data: Dict[str, Any]) -> str:
    """
    Attempt to guess roast level (V, K, S) from roast data.
    Based on weight loss and drop temperature.

    Light (V): ~11-13% loss, drop temp ~200-210°C
    Medium (K): ~13-15% loss, drop temp ~210-220°C
    Dark (S): ~15-18% loss, drop temp ~220-230°C
    """
    loss = roast_data.get('weight_loss_percent', 0)
    drop = roast_data.get('drop_temp', 0)

    # Check name first for explicit hints
    name = roast_data.get('roast_name', '').lower()
    if 'light' in name or 'világos' in name:
        return 'V'
    if 'dark' in name or 'sötét' in name:
        return 'S'
    if 'medium' in name or 'közép' in name:
        return 'K'

    # Guess from metrics
    if loss < 13 or drop < 210:
        return 'V'
    elif loss > 15 or drop > 220:
        return 'S'
    else:
        return 'K'


if __name__ == "__main__":
    # Test the module
    print(f"RoastTime path: {get_roasttime_path()}")
    summary = get_roast_summary()
    print(f"\nRoast Summary:")
    for key, value in summary.items():
        print(f"  {key}: {value}")
