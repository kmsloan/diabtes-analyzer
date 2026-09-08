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
# Last Shot Date (Tueday September 8, 2026)
LAST_SHOT_DATE = datetime(2026, 9, 8)

# Mode Toggle: Set to True for active 14-day cycle, False when paused
ON_MOUNJARO_SCHEDULE = True

# ============================================================================
# ISF & CR BASELINE SETTINGS
# ============================================================================
FRESH_SHOT_ISF = 36.0   # Days 0-3 Peak sensitivity (mg/dL/U)
FRESH_SHOT_CR = 10.0    # Days 0-3 Peak carb ratio (g/U)

MAX_RESIST_ISF = 22.0   # Day 12-14 / Off-shot max resistance ISF
MAX_RESIST_CR = 6.0     # Day 12-14 / Off-shot max resistance CR

# ============================================================================
# BASAL RATE SETTINGS (Nighttime vs Daytime)
# ============================================================================
NIGHTTIME_START = 22  # 10 PM
NIGHTTIME_END = 7     # 7 AM

NIGHTTIME_BASAL = {
    "fresh": 0.85,      # Fresh shot basal at night
    "resistant": 1.00   # Max resistance basal at night
}

DAYTIME_BASAL = {
    "fresh": 0.90,      # Fresh shot basal during day
    "resistant": 1.40   # Max resistance basal during day
}

# ============================================================================
# TARGET & BENCHMARK SETTINGS
# ============================================================================
TARGET_MGDL = 117.5
DRIFT_THRESHOLD = 50.0  # Smoothed: prevents single spikes from dominating


def calculate_basal_rate(fresh_rate: float, resistant_rate: float, resistance_adj: float) -> float:
    """Calculate recommended basal rate based on resistance adjustment."""
    rec_basal = fresh_rate + (resistant_rate - fresh_rate) * resistance_adj
    return round(rec_basal, 2)


def get_tidepool_data():
    """Logs into Tidepool API and fetches last 14 days of CGM data in mg/dL."""
    print("🔐 Logging into Tidepool API...")
    login_url = "https://api.tidepool.org/auth/login"
    
    res = requests.post(login_url, auth=(EMAIL, PASSWORD), timeout=10)
    if res.status_code != 200:
        raise Exception(f"Tidepool Login Failed ({res.status_code}): {res.text}")
    
    session_token = res.headers.get("x-tidepool-session-token")
    user_id = res.json().get("userid")
    print("✅ Successfully authenticated!")

    fourteen_days_ago = (datetime.now(timezone.utc) - timedelta(days=14)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    headers = {"x-tidepool-session-token": session_token}
    data_url = f"https://api.tidepool.org/data/{user_id}?type=cbg&startDate={fourteen_days_ago}"
    
    print("📥 Pulling last 14 days of Libre 3 data...")
    cbg_res = requests.get(data_url, headers=headers, timeout=15)
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

    # 3. Calculate Resistance Adjustment with Smoothing
    if ON_MOUNJARO_SCHEDULE:
        cycle_day = days_since_shot % 14
        
        # Days 0-3: Peak Sensitivity (Lock to Fresh Baseline)
        if cycle_day <= 3:
            final_adj = 0.0
        elif cycle_day <= 7:
            # Days 4-7: Early Waning (Dampened drift influence)
            cycle_base_adj = (cycle_day - 3) / 9.0
            drift = recent_avg - target_bg
            drift_adj = (drift / DRIFT_THRESHOLD) * 0.5
            final_adj = min(max(cycle_base_adj + drift_adj, 0.0), 1.0)
        else:
            # Days 8-13: Late Waning (Full drift response)
            cycle_base_adj = min((cycle_day - 3) / 9.0, 1.0)
            drift = recent_avg - target_bg
            drift_adj = drift / DRIFT_THRESHOLD
            final_adj = min(max(cycle_base_adj + drift_adj, 0.0), 1.0)
    else:
        # Off-Shot Mode
        cycle_day = days_since_shot
        drift = recent_avg - target_bg
        drift_adj = drift / DRIFT_THRESHOLD
        final_adj = min(max(1.0 + drift_adj, 0.0), 1.0)

    drift = recent_avg - target_bg

    # 4. Calculate Recommended Settings
    rec_isf = int(round(FRESH_SHOT_ISF - (FRESH_SHOT_ISF - MAX_RESIST_ISF) * final_adj))
    rec_cr = round(FRESH_SHOT_CR - (FRESH_SHOT_CR - MAX_RESIST_CR) * final_adj, 1)
    
    # 5. Calculate Basal Rates
    rec_night_basal = calculate_basal_rate(NIGHTTIME_BASAL["fresh"], NIGHTTIME_BASAL["resistant"], final_adj)
    rec_day_basal = calculate_basal_rate(DAYTIME_BASAL["fresh"], DAYTIME_BASAL["resistant"], final_adj)

    status_mode = f"Day {cycle_day} of 14 (Active 14-Day Cycle)" if ON_MOUNJARO_SCHEDULE else f"Day {days_since_shot} (Off-Shot Mode)"
    print(f"🗓️ {status_mode}")
    print(f"24h Avg: {recent_avg:.1f} mg/dL | Drift: {drift:+.1f} mg/dL | Resistance Adj: {final_adj:.2f}")
    print(f"🌙 Night (10 PM - 7 AM) Basal: {rec_night_basal} U/hr")
    print(f"☀️ Day (7 AM - 10 PM) Basal: {rec_day_basal} U/hr")

    # 6. Build Message
    if ON_MOUNJARO_SCHEDULE and cycle_day <= 3:
        header = f"💉 🟢 Peak Sensitivity Window (Day {cycle_day}/14)"
        action = "Peak Mounjaro active. Post-bolus only to match gastric delay:"
    elif not ON_MOUNJARO_SCHEDULE:
        header = f"⚠️ 💉 Mounjaro Off-Shot Mode (Day {days_since_shot})"
        action = f"24h Avg ({recent_avg:.1f} mg/dL). Holding off-shot resistance profile:"
    elif drift > 15.0:
        header = f"⚠️ 💉 Gradual Waning Drift Alert (Day {cycle_day}/14)"
        action = f"24h Avg ({recent_avg:.1f} mg/dL) elevated. Stepping settings smoothly:"
    elif drift < -10.0:
        header = f"🟢 💉 Glucose Relaxation Alert (Day {cycle_day}/14)"
        action = f"24h Avg ({recent_avg:.1f} mg/dL) low. Relaxing settings below baseline:"
    else:
        header = f"🟢 💉 On Track Cycle Profile (Day {cycle_day}/14)"
        action = f"24h Avg ({recent_avg:.1f} mg/dL) stable. Target Day {cycle_day} profile:"

    msg = (
        f"{header}\n"
        f"{action}\n"
        f"🎯 Set ISF: {rec_isf} mg/dL/U\n"
        f"🍕 Set CR: {rec_cr} g/U\n"
        f"🌙 Night (10 PM - 7 AM) Basal: {rec_night_basal} U/hr\n"
        f"☀️ Day (7 AM - 10 PM) Basal: {rec_day_basal} U/hr"
    )

    # 7. Send Push Notification
    try:
        res = requests.post(
            f"https://ntfy.sh/{NTFY_TOPIC}",
            data=msg.encode('utf-8'),
            headers={"Title": "Twiist Pump Profile Status"},
            timeout=10
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