import os
import requests
from datetime import datetime, timedelta, timezone

# ============================================================================
# CREDENTIALS & SETUP
# ============================================================================
EMAIL = os.environ.get("TIDEPOOL_EMAIL", "kmsloan4@gmail.com")
PASSWORD = os.environ.get("TIDEPOOL_PASSWORD", "Number4444!!")
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "kaitlin-twiist-alerts")

# ============================================================================
# MOUNJARO CYCLE CONFIGURATION
# ============================================================================
LAST_SHOT_DATE = datetime(2026, 9, 20)  # Update to your actual shot date
ON_MOUNJARO_SCHEDULE = True
CYCLE_DAYS = 7  # 7 for weekly, 14 for bi-weekly

# ============================================================================
# PUMP SETTING BOUNDARIES
# ============================================================================
# FRESH SHOT / SLOW DIGESTION (Days 0-2)
FRESH_SHOT_ISF = 38.0   
FRESH_SHOT_CR = 11.0    # Whole number; safe buffer against crashes

# MAXIMUM RESISTANCE / FAST DIGESTION (Days 5-7 or Off-Shot)
MAX_RESIST_ISF = 26.0   
MAX_RESIST_CR = 8.0     # Strong and responsive without jumping to 5

# Basal rates (snapped to 0.05 increments)
NIGHT_BASAL_FRESH = 0.85
NIGHT_BASAL_RESIST = 1.00
DAY_BASAL_FRESH = 0.95
DAY_BASAL_RESIST = 1.40

# ============================================================================
# CLINICAL TARGETS (24-Hour Performance Evaluation)
# ============================================================================
TARGET_MGDL = 115.0
DRIFT_CEILING = 145.0   # Only tighten if 24h average exceeds this threshold

def calculate_basal_rate(slow_rate: float, fast_rate: float, gear_shift: float) -> float:
    rec_basal = slow_rate + (fast_rate - slow_rate) * gear_shift
    return round(round(rec_basal * 20) / 20.0, 2)

def get_tidepool_data():
    print("🔐 Logging into Tidepool API...")
    login_url = "https://api.tidepool.org/auth/login"

    res = requests.post(login_url, auth=(EMAIL, PASSWORD), timeout=10)
    if res.status_code != 200:
        raise Exception(f"Tidepool Login Failed ({res.status_code}): {res.text}")

    session_token = res.headers.get("x-tidepool-session-token")
    user_id = res.json().get("userid")

    two_days_ago = (datetime.now(timezone.utc) - timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    headers = {"x-tidepool-session-token": session_token}
    data_url = f"https://api.tidepool.org/data/{user_id}?type=cbg&startDate={two_days_ago}"

    cbg_res = requests.get(data_url, headers=headers, timeout=15)
    cbg_data = cbg_res.json() if cbg_res.status_code == 200 else []

    valid_entries = []
    for entry in cbg_data:
        if "value" in entry and "time" in entry:
            val_mgdl = entry["value"] * 18.0182 if entry["value"] < 30 else entry["value"]
            time_str = entry["time"].replace("Z", "+00:00")
            valid_entries.append({"time": datetime.fromisoformat(time_str), "value": val_mgdl})

    twenty_four_hours_ago = datetime.now(timezone.utc) - timedelta(hours=24)
    readings_24h, daytime_readings_24h = [], []

    for e in valid_entries:
        if e["time"] >= twenty_four_hours_ago:
            readings_24h.append(e["value"])
            local_hour = (e["time"] - timedelta(hours=4)).hour  # EDT conversion
            if 7 <= local_hour < 22:
                daytime_readings_24h.append(e["value"])

    if not readings_24h:
        return TARGET_MGDL, 0.0

    avg_24h = sum(readings_24h) / len(readings_24h)
    daytime_low_pct = (sum(1 for v in daytime_readings_24h if v < 70.0) / len(daytime_readings_24h)) * 100.0 if daytime_readings_24h else 0.0

    return avg_24h, daytime_low_pct

def analyze():
    avg_24h, daytime_low_pct = get_tidepool_data()
    today = datetime.now()
    days_since_shot = (today - LAST_SHOT_DATE).days
    cycle_day = days_since_shot % CYCLE_DAYS if ON_MOUNJARO_SCHEDULE else days_since_shot

    # Base schedule gear (0.0 = Fresh Shot / Slow, 1.0 = End of Cycle / Resistant)
    if ON_MOUNJARO_SCHEDULE and cycle_day <= 2:
        gear_base = 0.0
    elif ON_MOUNJARO_SCHEDULE:
        gear_base = min((cycle_day - 2) / 4.0, 1.0)
    else:
        gear_base = 1.0

    # Safety override and resistance fine-tuning
    if daytime_low_pct >= 4.0:
        gear_shift = max(gear_base - 0.5, 0.0)
        status_note = f"🟢 Daytime Lows ({daytime_low_pct:.1f}%). Softening upfront insulin to protect against crashes."
    elif avg_24h <= DRIFT_CEILING:
        gear_shift = gear_base
        status_note = f"🟢 Controlled range (Avg {avg_24h:.1f} mg/dL). Holding scheduled gear."
    else:
        excess = min(avg_24h - DRIFT_CEILING, 40.0)
        gear_shift = min(gear_base + (excess / 40.0) * 0.5, 1.0)
        status_note = f"⚠️ High glucose drift (Avg {avg_24h:.1f} mg/dL). Tightening profile."

    # Compute final whole-number CR and 0.05-increment basals
    rec_isf = int(round(FRESH_SHOT_ISF - (FRESH_SHOT_ISF - MAX_RESIST_ISF) * gear_shift))
    rec_cr = int(round(FRESH_SHOT_CR - (FRESH_SHOT_CR - MAX_RESIST_CR) * gear_shift))
    rec_night_basal = calculate_basal_rate(NIGHT_BASAL_FRESH, NIGHT_BASAL_RESIST, gear_shift)
    rec_day_basal = calculate_basal_rate(DAY_BASAL_FRESH, DAY_BASAL_RESIST, gear_shift)

    msg = (
        f"💉 Mounjaro Day {cycle_day}/{CYCLE_DAYS}\n"
        f"{status_note}\n"
        f"🎯 Set ISF: {rec_isf} mg/dL/U\n"
        f"🍕 Set CR: {rec_cr} g/U\n"
        f"🌙 Night Basal: {rec_night_basal:.2f} U/hr\n"
        f"☀️ Day Basal: {rec_day_basal:.2f} U/hr"
    )

    try:
        res = requests.post(f"https://ntfy.sh/{NTFY_TOPIC}", data=msg.encode('utf-8'), headers={"Title": "Twiist Loop Update"})
        if res.status_code == 200:
            print("📲 Push notification sent successfully!")
            print(f"\n--- Preview ---\n{msg}\n---------------")
    except Exception as e:
        print(f"❌ Error: {e}")

if __name__ == "__main__":
    analyze()