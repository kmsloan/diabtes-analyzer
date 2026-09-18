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
LAST_SHOT_DATE = datetime(2026, 9, 8)

ON_MOUNJARO_SCHEDULE = True
CYCLE_DAYS = 7  # 7 FOR WEEKLY SHOTS, OR 14 FOR BI-WEEKLY

# ============================================================================
# ISF & CR BASELINE SETTINGS
# ============================================================================
FRESH_SHOT_ISF = 36.0   
FRESH_SHOT_CR = 10.0    # Whole numbers only

MAX_RESIST_ISF = 22.0   
MAX_RESIST_CR = 6.0     

# ============================================================================
# BASAL RATE SETTINGS (Nighttime vs Daytime)
# ============================================================================
NIGHTTIME_START = 22  
NIGHTTIME_END = 7     

NIGHTTIME_BASAL = {"fresh": 0.85, "resistant": 1.00}
DAYTIME_BASAL = {"fresh": 0.90, "resistant": 1.40}

# ============================================================================
# TARGET & BENCHMARK SETTINGS
# ============================================================================
TARGET_MGDL = 117.5
DRIFT_THRESHOLD = 50.0  

def calculate_basal_rate(fresh_rate: float, resistant_rate: float, resistance_adj: float) -> float:
    rec_basal = fresh_rate + (resistant_rate - fresh_rate) * resistance_adj
    # 🟢 Round to the nearest 0.05
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

    fourteen_days_ago = (datetime.now(timezone.utc) - timedelta(days=14)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    headers = {"x-tidepool-session-token": session_token}
    data_url = f"https://api.tidepool.org/data/{user_id}?type=cbg&startDate={fourteen_days_ago}"
    
    print("📥 Pulling recent CGM readings...")
    cbg_res = requests.get(data_url, headers=headers, timeout=15)
    cbg_data = cbg_res.json() if cbg_res.status_code == 200 else []

    valid_entries = []
    for entry in cbg_data:
        if "value" in entry and "time" in entry:
            val_mgdl = entry["value"] * 18.0182
            time_str = entry["time"].replace("Z", "+00:00")
            dt = datetime.fromisoformat(time_str)
            valid_entries.append({"time": dt, "value": val_mgdl})

    now_utc = datetime.now(timezone.utc)
    twenty_four_hours_ago = now_utc - timedelta(hours=24)

    recent_readings = [e["value"] for e in valid_entries if e["time"] >= twenty_four_hours_ago]
    recent_avg = sum(recent_readings) / len(recent_readings) if recent_readings else TARGET_MGDL

    return TARGET_MGDL, recent_avg

def analyze():
    target_bg, recent_avg = get_tidepool_data()
    today = datetime.now()
    days_since_shot = (today - LAST_SHOT_DATE).days

    if ON_MOUNJARO_SCHEDULE:
        cycle_day = days_since_shot % CYCLE_DAYS
        
        if CYCLE_DAYS == 7:
            if cycle_day <= 2:
                final_adj = 0.0
            else:
                cycle_base_adj = (cycle_day - 2) / 4.0
                drift_adj = (recent_avg - target_bg) / DRIFT_THRESHOLD
                final_adj = min(max(cycle_base_adj + drift_adj, 0.0), 1.0)
        else:
            if cycle_day <= 3:
                final_adj = 0.0
            elif cycle_day <= 7:
                cycle_base_adj = (cycle_day - 3) / 9.0
                drift_adj = ((recent_avg - target_bg) / DRIFT_THRESHOLD) * 0.5
                final_adj = min(max(cycle_base_adj + drift_adj, 0.0), 1.0)
            else:
                cycle_base_adj = min((cycle_day - 3) / 9.0, 1.0)
                drift_adj = (recent_avg - target_bg) / DRIFT_THRESHOLD
                final_adj = min(max(cycle_base_adj + drift_adj, 0.0), 1.0)
    else:
        cycle_day = days_since_shot
        drift = recent_avg - target_bg
        drift_adj = drift / DRIFT_THRESHOLD
        final_adj = min(max(1.0 + drift_adj, 0.0), 1.0)

    drift = recent_avg - target_bg

    rec_isf = int(round(FRESH_SHOT_ISF - (FRESH_SHOT_ISF - MAX_RESIST_ISF) * final_adj))
    rec_cr = int(round(FRESH_SHOT_CR - (FRESH_SHOT_CR - MAX_RESIST_CR) * final_adj))
    
    rec_night_basal = calculate_basal_rate(NIGHTTIME_BASAL["fresh"], NIGHTTIME_BASAL["resistant"], final_adj)
    rec_day_basal = calculate_basal_rate(DAYTIME_BASAL["fresh"], DAYTIME_BASAL["resistant"], final_adj)

    status_mode = f"Day {cycle_day} of {CYCLE_DAYS} ({CYCLE_DAYS}-Day Cycle)" if ON_MOUNJARO_SCHEDULE else f"Day {days_since_shot} (Off-Shot Mode)"
    
    if ON_MOUNJARO_SCHEDULE and cycle_day <= (2 if CYCLE_DAYS == 7 else 3):
        header = f"💉 🟢 Peak Sensitivity Window (Day {cycle_day}/{CYCLE_DAYS})"
    elif not ON_MOUNJARO_SCHEDULE:
        header = f"⚠️ 💉 Mounjaro Off-Shot Mode (Day {days_since_shot})"
    elif drift > 15.0:
        header = f"⚠️ 💉 Gradual Waning Drift Alert (Day {cycle_day}/{CYCLE_DAYS})"
    elif drift < -10.0:
        header = f"🟢 💉 Glucose Relaxation Alert (Day {cycle_day}/{CYCLE_DAYS})"
    else:
        header = f"🟢 💉 On Track Cycle Profile (Day {cycle_day}/{CYCLE_DAYS})"

    # 🟢 Format basal rates to always show two decimal places (e.g., 0.90)
    msg = (
        f"{header}\n"
        f"🎯 Set ISF: {rec_isf} mg/dL/U\n"
        f"🍕 Set CR: {rec_cr} g/U\n"
        f"🌙 Night (10 PM - 7 AM) Basal: {rec_night_basal:.2f} U/hr\n"
        f"☀️ Day (7 AM - 10 PM) Basal: {rec_day_basal:.2f} U/hr"
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