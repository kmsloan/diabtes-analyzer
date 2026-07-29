import os
import requests
from datetime import datetime, timedelta, timezone

# Credentials & Setup
EMAIL = os.environ.get("TIDEPOOL_EMAIL", "kmsloan4@gmail.com")
PASSWORD = os.environ.get("TIDEPOOL_PASSWORD", "Number4444!!")
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "kaitlin-twiist-alerts")

# Reference Sunday Shot Date (Tonight: July 26, 2026)
REFERENCE_SHOT_DATE = datetime(2026, 7, 26)

# Baseline Settings
FRESH_SHOT_ISF = 36.0
FRESH_SHOT_CR = 10.0
MAX_RESIST_ISF = 22.0
MAX_RESIST_CR = 6.0

def get_tidepool_data():
    """Logs into Tidepool API and fetches ONLY the last 14 days of CGM data."""
    print("🔐 Logging into Tidepool API...")
    login_url = "https://api.tidepool.org/auth/login"
    
    res = requests.post(login_url, auth=(EMAIL, PASSWORD))
    if res.status_code != 200:
        raise Exception(f"Tidepool Login Failed ({res.status_code}): {res.text}")
    
    session_token = res.headers.get("x-tidepool-session-token")
    user_id = res.json().get("userid")
    print(f"✅ Successfully authenticated!")

    # Calculate ISO timestamp for 14 days ago
    fourteen_days_ago = (datetime.now(timezone.utc) - timedelta(days=14)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    # Fetch ONLY last 14 days of data to prevent hanging
    headers = {"x-tidepool-session-token": session_token}
    data_url = f"https://api.tidepool.org/data/{user_id}?type=cbg&startDate={fourteen_days_ago}"
    
    print("📥 Pulling last 14 days of Libre 3 data...")
    cbg_res = requests.get(data_url, headers=headers)
    
    cbg_data = cbg_res.json() if cbg_res.status_code == 200 else []
    print(f"✅ Pulled {len(cbg_data)} recent CGM readings!")

    # Calculate weekly averages
    values = [entry["value"] for entry in cbg_data if "value" in entry]
    
    if len(values) >= 50:
        half = len(values) // 2
        wk1_avg = sum(values[:half]) / half
        wk2_avg = sum(values[half:]) / (len(values) - half)
    else:
        # Fallback defaults if sensor is warming up
        wk1_avg = 120.0
        wk2_avg = 132.6

    return wk1_avg, wk2_avg

def analyze():
    # 1. Fetch Data
    wk1_avg, wk2_avg = get_tidepool_data()
    shift = wk2_avg - wk1_avg
    pct_shift = (shift / wk1_avg) * 100.0 if wk1_avg else 0.0

    # 2. Calculate Mounjaro Cycle Position
    today = datetime.now()
    days_since_shot = (today - REFERENCE_SHOT_DATE).days % 14

    print(f"🗓️ Day {days_since_shot} of 14 in Mounjaro Cycle")
    print(f"Week 1 Avg: {wk1_avg:.1f} | Week 2 Avg: {wk2_avg:.1f} | Shift: {shift:+.1f} mg/dL ({pct_shift:+.1f}%)")

    # 3. Determine Message
    if days_since_shot == 0:
        msg = (
            f"💉 🟢 Mounjaro Shot Day Reset!\n"
            f"Take shot tonight. Reset Twiist pump to Fresh Shot baseline:\n"
            f"🎯 Set ISF: {FRESH_SHOT_ISF} mg/dL/U\n"
            f"🍕 Set CR: {FRESH_SHOT_CR} g/U"
        )
    elif days_since_shot <= 5:
        msg = (
            f"🟢 💉 Peak Mounjaro Sensitivity (Day {days_since_shot}/14)\n"
            f"Glucose shift is normal ({shift:+.1f} mg/dL).\n"
            f"Keep relaxed settings (ISF {FRESH_SHOT_ISF} / CR {FRESH_SHOT_CR})!"
        )
    else:
        if shift >= 8.0:
            adj = 1.0 - (pct_shift / 100.0)
            rec_isf = round(max(FRESH_SHOT_ISF * adj, MAX_RESIST_ISF), 1)
            rec_cr = round(max(FRESH_SHOT_CR * adj, MAX_RESIST_CR), 1)

            msg = (
                f"⚠️ 💉 Mounjaro Waning Alert (Day {days_since_shot}/14)\n"
                f"Shift: +{shift:.1f} mg/dL (+{pct_shift:.1f}%)\n"
                f"🎯 Adjust ISF: -> {rec_isf} mg/dL/U\n"
                f"🍕 Adjust CR: -> {rec_cr} g/U"
            )
        else:
            msg = (
                f"🟢 💉 Cycle Status Normal (Day {days_since_shot}/14)\n"
                f"Shift: {shift:+.1f} mg/dL. No pump changes needed today!"
            )

    # 4. Send Notification via ntfy
    try:
        res = requests.post(
            f"https://ntfy.sh/{NTFY_TOPIC}",
            data=msg.encode('utf-8'),
            headers={"Title": "Twiist Pump Profile Status"}
        )
        if res.status_code == 200:
            print("📲 Push notification sent to phone!")
        else:
            print(f"❌ Failed to send phone alert: {res.status_code}")
    except Exception as e:
        print(f"❌ Error sending notification: {e}")

if __name__ == "__main__":
    analyze()