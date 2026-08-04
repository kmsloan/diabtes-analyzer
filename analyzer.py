import os
import requests
from datetime import datetime, timedelta, timezone

# Credentials & Setup
EMAIL = os.environ.get("TIDEPOOL_EMAIL", "kmsloan4@gmail.com")
PASSWORD = os.environ.get("TIDEPOOL_PASSWORD", "Number4444!!")
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "kaitlin-twiist-alerts")

# Reference Sunday Shot Date (Sunday July 26, 2026)
REFERENCE_SHOT_DATE = datetime(2026, 7, 26)

# Baseline Settings Range
FRESH_SHOT_ISF = 36.0   # Day 0-3 Peak Sensitivity
FRESH_SHOT_CR = 10.0

MAX_RESIST_ISF = 22.0   # Day 12-13 Max Resistance
MAX_RESIST_CR = 6.0

# Target Average Glucose Benchmark
TARGET_MGDL = 117.5

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
    
    # 2. Calculate Mounjaro Cycle Position (0 to 13)
    today = datetime.now()
    days_since_shot = (today - REFERENCE_SHOT_DATE).days % 14

    # 3. Calculate Cycle-Aware Baseline Resistance (0.0 to 1.0)
    # Days 0-3 = 0.0 (Fresh shot); Days 4-12 ramp smoothly to 1.0 (Max resistance)
    if days_since_shot <= 3:
        cycle_base_adj = 0.0
    else:
        cycle_base_adj = min((days_since_shot - 3) / 9.0, 1.0)

    # 4. Apply 24h Glucose Modifier (+/- adjustment)
    drift = recent_avg - target_bg
    drift_adj = drift / 25.0  # +10 mg/dL drift adds +0.40 resistance modifier
    
    # Combined adjustment clamped between 0.0 and 1.0
    final_adj = min(max(cycle_base_adj + drift_adj, 0.0), 1.0)

    rec_isf = int(round(FRESH_SHOT_ISF - (FRESH_SHOT_ISF - MAX_RESIST_ISF) * final_adj))
    rec_cr = round(FRESH_SHOT_CR - (FRESH_SHOT_CR - MAX_RESIST_CR) * final_adj, 1)

    print(f"🗓️ Day {days_since_shot} of 14 in Mounjaro Cycle")
    print(f"24h Avg: {recent_avg:.1f} mg/dL | Drift: {drift:+.1f} mg/dL | Cycle Base: {cycle_base_adj:.2f}")

    # 5. Build Message
    if days_since_shot == 0:
        header = "💉 🟢 Mounjaro Shot Day Reset!"
        action = "Take shot tonight. Set Twiist pump to Fresh Shot baseline:"
    elif drift > 10.0:
        header = f"⚠️ 💉 High Glucose Drift Alert (Day {days_since_shot}/14)"
        action = f"24h Avg ({recent_avg:.1f} mg/dL) is elevated. Tightening settings beyond Day {days_since_shot} baseline:"
    elif drift < -10.0:
        header = f"🟢 💉 Low Glucose Relaxation Alert (Day {days_since_shot}/14)"
        action = f"24h Avg ({recent_avg:.1f} mg/dL) is low. Relaxing settings below Day {days_since_shot} baseline:"
    else:
        header = f"🟢 💉 On Track Cycle Profile (Day {days_since_shot}/14)"
        action = f"24h Avg ({recent_avg:.1f} mg/dL) is stable. Target Day {days_since_shot} profile:"

    msg = (
        f"{header}\n"
        f"{action}\n"
        f"🎯 Set ISF: {rec_isf} mg/dL/U\n"
        f"🍕 Set CR: {rec_cr} g/U"
    )

    # 6. Send Push Notification
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