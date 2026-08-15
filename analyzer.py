import os
import requests
from datetime import datetime, timedelta, timezone

# ============================================================================
# CREDENTIALS & SETUP (use environment variables only)
# ============================================================================
EMAIL = os.environ.get("TIDEPOOL_EMAIL", "kmsloan4@gmail.com")
PASSWORD = os.environ.get("TIDEPOOL_PASSWORD", "Number4444!!")
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "kaitlin-twiist-alerts")

# ============================================================================
# MOUNJARO CYCLE CONFIGURATION
# ============================================================================
# Last Shot Date (Sunday July 26, 2026)
LAST_SHOT_DATE = datetime(2026, 7, 26)

# Mode Toggle: Set to False while paused/waiting for refill
ON_MOUNJARO_SCHEDULE = False

# ============================================================================
# ISF & CR BASELINE SETTINGS RANGE
# ============================================================================
FRESH_SHOT_ISF = 36.0   # Fresh shot sensitivity (mg/dL/U)
FRESH_SHOT_CR = 10.0    # Fresh shot carb ratio (g/U)

MAX_RESIST_ISF = 22.0   # Off-shot / Max resistance ISF
MAX_RESIST_CR = 6.0     # Off-shot / Max resistance CR

# ============================================================================
# BASAL RATE SETTINGS (Nighttime vs Daytime)
# ============================================================================
# Nighttime: typically 10 PM to 7 AM (lower basal rates)
# Daytime: typically 7 AM to 10 PM (higher basal rates from your pump screenshot)

NIGHTTIME_START = 22  # 10 PM
NIGHTTIME_END = 7     # 7 AM

# Nighttime basal rates (capped lower - max 1.0 U/hr)
NIGHTTIME_BASAL = {
    "fresh": 0.9,       # Fresh shot basal at night
    "resistant": 1.0    # Off-shot/resistant basal at night (max 1.0)
}

# Daytime basal rates (from your pump screenshot)
DAYTIME_BASAL = {
    "fresh": 0.9,       # Fresh shot basal during day
    "resistant": 1.4    # Off-shot/resistant basal during day
}

# ============================================================================
# TARGET & BENCHMARK SETTINGS
# ============================================================================
TARGET_MGDL = 117.5

# Drift Sensitivity: How much to adjust for each 25 mg/dL of drift
DRIFT_THRESHOLD = 25.0

def get_current_basal_rate_profile() -> dict:
    """
    Get the current basal rate based on whether it's nighttime or daytime.
    
    Returns:
        Dictionary with "fresh" and "resistant" basal rates
    """
    now = datetime.now()
    current_hour = now.hour
    
    # Check if it's nighttime (10 PM to 7 AM)
    if current_hour >= NIGHTTIME_START or current_hour < NIGHTTIME_END:
        return NIGHTTIME_BASAL
    else:
        return DAYTIME_BASAL


def calculate_basal_rate(fresh_rate: float, resistant_rate: float, resistance_adj: float) -> float:
    """
    Calculate recommended basal rate based on resistance adjustment.
    
    Args:
        fresh_rate: Basal rate when fresh on Mounjaro (low resistance)
        resistant_rate: Basal rate when off-shot or at peak resistance
        resistance_adj: Resistance adjustment factor (0.0 to 1.0)
    
    Returns:
        Recommended basal rate in U/hr
    """
    # Interpolate between fresh and resistant rates
    rec_basal = fresh_rate + (resistant_rate - fresh_rate) * resistance_adj
    return round(rec_basal, 2)


def get_tidepool_data():
    """Logs into Tidepool API and fetches last 14 days of CGM data in mg/dL."""
    print("🔐 Logging into Tidepool API...")
    login_url = "https://api.tidepool.org/auth/login"
    
    res = requests.post(login_url, auth=(EMAIL, PASSWORD))
    if res.status_code != 200:
        raise Exception(f"Tidepool Login Failed ({res.status_code}): {res.text}")
    
    session_token = res.headers.get("x-tidepool-session-token")
    user_id = res.json().get("userid")
    print("✅ Successfully authenticated!")

    fourteen_days_ago = (datetime.now(timezone.utc) - timedelta(days=14)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    headers = {"x-tidepool-session-token": session_token}
    data_url = f"https://api.tidepool.org/data/{user_id}?type=cbg&startDate={fourteen_days_ago}"
    
    print("📥 Pulling last 14 days of Libre 3 data...")
    cbg_res = requests.get(data_url, headers=headers)
    cbg_data = cbg_res.json() if cbg_res.status_code == 200 else []
    print(f"✅ Pulled {len(cbg_data)} recent CGM readings!")

    # Parse ISO dates and convert mmol/L to mg/dL
    valid_entries = []
    for entry in cbg_data:
        if "value" in entry and "time" in entry:
            val_mgdl = entry["value"] * 18.0182
            time_str = entry["time"].replace("Z", "+00:00")
            dt = datetime.fromisoformat(time_str)
            valid_entries.append({"time": dt, "value": val_mgdl})

    now_utc = datetime.now(timezone.utc)
    twenty_four_hours_ago = now_utc - timedelta(hours=24)

    # Filter last 24 hours average
    recent_readings = [e["value"] for e in valid_entries if e["time"] >= twenty_four_hours_ago]

    if recent_readings:
        recent_avg = sum(recent_readings) / len(recent_readings)
    else:
        recent_avg = TARGET_MGDL

    return TARGET_MGDL, recent_avg

def analyze():
    # 1. Fetch Data
    target_bg, recent_avg = get_tidepool_data()
    
    # 2. Calculate Days Since Last Shot
    today = datetime.now()
    days_since_shot = (today - LAST_SHOT_DATE).days

    # 3. Calculate Baseline Resistance
    if ON_MOUNJARO_SCHEDULE:
        # Standard 14-day cycle ramp
        cycle_day = days_since_shot % 14
        if cycle_day <= 3:
            cycle_base_adj = 0.0
        else:
            cycle_base_adj = min((cycle_day - 3) / 9.0, 1.0)
    else:
        # OFF-SHOT MODE: Lock baseline to max resistance (1.0)
        cycle_base_adj = 1.0

    # 4. Apply 24h Glucose Modifier (+/- adjustment)
    drift = recent_avg - target_bg
    drift_adj = drift / DRIFT_THRESHOLD
    
    final_adj = min(max(cycle_base_adj + drift_adj, 0.0), 1.0)

    # 5. Calculate Recommended Settings
    rec_isf = int(round(FRESH_SHOT_ISF - (FRESH_SHOT_ISF - MAX_RESIST_ISF) * final_adj))
    rec_cr = round(FRESH_SHOT_CR - (FRESH_SHOT_CR - MAX_RESIST_CR) * final_adj, 1)
    
    # 6. Calculate Both Nighttime and Daytime Basal Rates
    rec_night_basal = calculate_basal_rate(
        NIGHTTIME_BASAL["fresh"],
        NIGHTTIME_BASAL["resistant"],
        final_adj
    )
    rec_day_basal = calculate_basal_rate(
        DAYTIME_BASAL["fresh"],
        DAYTIME_BASAL["resistant"],
        final_adj
    )

    print(f"🗓️ Day {days_since_shot} Since Last Mounjaro Shot (Off-Shot Mode)")
    print(f"24h Avg: {recent_avg:.1f} mg/dL | Drift: {drift:+.1f} mg/dL | Resistance Adj: {final_adj:.2f}")
    print(f"🌙 Night (10 PM - 7 AM) Basal: {rec_night_basal} U/hr")
    print(f"☀️ Day (7 AM - 10 PM) Basal: {rec_day_basal} U/hr")

    # 7. Build Message
    if not ON_MOUNJARO_SCHEDULE:
        header = f"⚠️ 💉 Mounjaro Off-Shot Pause (Day {days_since_shot})"
        if drift > 10.0:
            action = f"24h Avg ({recent_avg:.1f} mg/dL) elevated without Mounjaro. Max resistance active:"
        else:
            action = f"24h Avg ({recent_avg:.1f} mg/dL) holding. Recommended off-shot profile:"
    else:
        header = f"🟢 💉 On Track Cycle Profile (Day {days_since_shot}/14)"
        action = f"24h Avg ({recent_avg:.1f} mg/dL) is stable. Target profile:"

    msg = (
        f"{header}\n"
        f"{action}\n"
        f"🎯 Set ISF: {rec_isf} mg/dL/U\n"
        f"🍕 Set CR: {rec_cr} g/U\n"
        f"🌙 Night (10 PM - 7 AM) Basal: {rec_night_basal} U/hr\n"
        f"☀️ Day (7 AM - 10 PM) Basal: {rec_day_basal} U/hr"
    )

    # 8. Send Push Notification
    try:
        res = requests.post(
            f"https://ntfy.sh/{NTFY_TOPIC}",
            data=msg.encode('utf-8'),
            headers={"Title": "Twiist Pump Profile Status"}
        )
        if res.status_code == 200:
            print("📲 Push notification sent to phone!")
            print(f"Message content:\n{msg}")
        else:
            print(f"❌ Failed to send phone alert: {res.status_code}")
    except Exception as e:
        print(f"❌ Error sending notification: {e}")

if __name__ == "__main__":
    analyze()