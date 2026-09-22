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
LAST_SHOT_DATE = datetime(2026, 9, 20)  # Update to your most recent shot date
ON_MOUNJARO_SCHEDULE = True
CYCLE_DAYS = 7  # 7 for weekly, 14 for bi-weekly

# ============================================================================
# PUMP SETTING BOUNDARIES
# ============================================================================
FRESH_SHOT_ISF = 36.0   
FRESH_SHOT_CR = 10.0    # Whole numbers only

MAX_RESIST_ISF = 22.0   
MAX_RESIST_CR = 6.0     

# Basal settings (0.05 step increments)
NIGHTTIME_BASAL = {"fresh": 0.85, "resistant": 1.00}
DAYTIME_BASAL = {"fresh": 0.90, "resistant": 1.40}

# ============================================================================
# CLINICAL TARGETS (24-Hour Performance Evaluation)
# ============================================================================
TARGET_MGDL = 115.0
DRIFT_CEILING = 145.0   # Only tighten if 24h average exceeds this threshold

def calculate_basal_rate(fresh_rate: float, resistant_rate: float, resistance_adj: float) -> float:
    rec_basal = fresh_rate + (resistant_rate - fresh_rate) * resistance_adj
    return round(round(rec_basal * 20) / 20.0, 2)

def get_tidepool_data():
    print("🔐 Logging into Tidepool API...")
    login_url = "https://api.tidepool.org/auth/login"
    
    res = requests.post(login_url, auth=(EMAIL, PASSWORD), timeout=10)
    if res.status_code != 200:
        raise Exception(f"Tidepool Login Failed ({res.status_code}): {res.text}")
    
    session_token = res.headers.get("x-tidepool-session-token")
    user_id = res.json().get("userid")
    print("✅ Successfully authenticated!")

    # Pull last 4 days to ensure full 24-hour window coverage
    four_days_ago = (datetime.now(timezone.utc) - timedelta(days=4)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    headers = {"x-tidepool-session-token": session_token}
    data_url = f"https://api.tidepool.org/data/{user_id}?type=cbg&startDate={four_days_ago}"
    
    print("📥 Pulling recent CGM readings...")
    cbg_res = requests.get(data_url, headers=headers, timeout=15)
    cbg_data = cbg_res.json() if cbg_res.status_code == 200 else []

    valid_entries = []
    for entry in cbg_data:
        if "value" in entry and "time" in entry:
            # Tidepool values in mmol/L -> convert to mg/dL
            val_mgdl = entry["value"] * 18.0182 if entry["value"] < 30 else entry["value"]
            time_str = entry["time"].replace("Z", "+00:00")
            dt = datetime.fromisoformat(time_str)
            valid_entries.append({"time": dt, "value": val_mgdl})

    now_utc = datetime.now(timezone.utc)
    twenty_four_hours_ago = now_utc - timedelta(hours=24)

    readings_24h = [e["value"] for e in valid_entries if e["time"] >= twenty_four_hours_ago]
    
    if not readings_24h:
        return TARGET_MGDL, 0.0

    avg_24h = sum(readings_24h) / len(readings_24h)
    low_pct_24h = (sum(1 for v in readings_24h if v < 70.0) / len(readings_24h)) * 100.0

    return avg_24h, low_pct_24h

def analyze():
    avg_24h, low_pct_24h = get_tidepool_data()
    today = datetime.now()
    days_since_shot = (today - LAST_SHOT_DATE).days
    cycle_day = days_since_shot % CYCLE_DAYS if ON_MOUNJARO_SCHEDULE else days_since_shot

    print(f"📊 24-Hour Performance: Avg = {avg_24h:.1f} mg/dL | Lows (<70) = {low_pct_24h:.1f}%")

    # ========================================================================
    # PERFORMANCE-DRIVEN LOGIC (NO CALENDAR-FORCED RAMP)
    # ========================================================================
    if low_pct_24h >= 3.0 or avg_24h < 105.0:
        # 🔴 Safety First: Recent lows or running low -> Full baseline relaxation
        final_adj = 0.0
        status_note = f"🟢 Lows detected ({low_pct_24h:.1f}% time < 70). Preserving baseline to prevent crashes."
    elif avg_24h <= DRIFT_CEILING:
        # 🟢 Sweet Spot: Average is between 105 and 145 mg/dL -> Keep baseline/moderate
        # Proportional gentle nudge only if between 125 and 145
        if avg_24h > 125.0:
            final_adj = (avg_24h - 125.0) / (DRIFT_CEILING - 125.0) * 0.35  # max 35% mild adjustment
            status_note = f"🟢 Controlled range (Avg {avg_24h:.1f} mg/dL). Mild sensitivity fine-tuning."
        else:
            final_adj = 0.0
            status_note = f"🟢 Excellent control (Avg {avg_24h:.1f} mg/dL). Settings are working well."
    else:
        # ⚠️ Genuine Resistance: 24h avg > 145 mg/dL without lows -> Scale resistance
        excess_drift = min(avg_24h - DRIFT_CEILING, 40.0)
        final_adj = min(0.35 + (excess_drift / 40.0) * 0.65, 1.0)
        status_note = f"⚠️ Persistent high drift (Avg {avg_24h:.1f} mg/dL). Adjusting for resistance."

    # Calculate final pump values
    rec_isf = int(round(FRESH_SHOT_ISF - (FRESH_SHOT_ISF - MAX_RESIST_ISF) * final_adj))
    rec_cr = int(round(FRESH_SHOT_CR - (FRESH_SHOT_CR - MAX_RESIST_CR) * final_adj))
    rec_night_basal = calculate_basal_rate(NIGHTTIME_BASAL["fresh"], NIGHTTIME_BASAL["resistant"], final_adj)
    rec_day_basal = calculate_basal_rate(DAYTIME_BASAL["fresh"], DAYTIME_BASAL["resistant"], final_adj)

    header = f"💉 Day {cycle_day}/{CYCLE_DAYS} Profile Review"

    msg = (
        f"{header}\n"
        f"{status_note}\n"
        f"🎯 Set ISF: {rec_isf} mg/dL/U\n"
        f"🍕 Set CR: {rec_cr} g/U\n"
        f"🌙 Night Basal: {rec_night_basal:.2f} U/hr\n"
        f"☀️ Day Basal: {rec_day_basal:.2f} U/hr"
    )

    try:
        res = requests.post(
            f"https://ntfy.sh/{NTFY_TOPIC}",
            data=msg.encode('utf-8'),
            headers={"Title": "Twiist Pump Profile Status"},
            timeout=10
        )
        if res.status_code == 200:
            print("📲 Push notification sent to phone!")
        else:
            print(f"❌ Failed to send phone alert: {res.status_code}")
    except Exception as e:
        print(f"❌ Error sending notification: {e}")

if __name__ == "__main__":
    analyze()