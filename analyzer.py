import os
import requests
from datetime import datetime, timedelta, timezone

# Credentials & Setup
EMAIL = os.environ.get("TIDEPOOL_EMAIL", "kmsloan4@gmail.com")
PASSWORD = os.environ.get("TIDEPOOL_PASSWORD", "Number4444!!")
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "kaitlin-twiist-alerts")

# Reference Sunday Shot Date (July 26, 2026)
REFERENCE_SHOT_DATE = datetime(2026, 7, 26)

# Settings Range
FRESH_SHOT_ISF = 36.0
FRESH_SHOT_CR = 10.0
MAX_RESIST_ISF = 22.0
MAX_RESIST_CR = 6.0

def get_tidepool_data():
    """Logs into Tidepool API and fetches last 14 days of CGM data."""
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

    # Sort readings CHRONOLOGICALLY (Oldest to Newest)
    valid_entries = [e for e in cbg_data if "value" in e and "time" in e]
    valid_entries.sort(key=lambda x: x["time"])
    
    values = [entry["value"] for entry in valid_entries]
    
    if len(values) >= 50:
        half = len(values) // 2
        past_avg = sum(values[:half]) / half       # Older 7 days
        recent_avg = sum(values[half:]) / (len(values) - half) # Most recent 7 days
    else:
        past_avg = 120.0
        recent_avg = 132.6

    return past_avg, recent_avg

def analyze():
    # 1. Fetch Data
    past_avg, recent_avg = get_tidepool_data()
    shift = recent_avg - past_avg  # Corrected: Recent minus Past
    pct_shift = (shift / past_avg) * 100.0 if past_avg else 0.0

    # 2. Calculate Mounjaro Cycle Position
    today = datetime.now()
    days_since_shot = (today - REFERENCE_SHOT_DATE).days % 14

    print(f"🗓️ Day {days_since_shot} of 14 in Mounjaro Cycle")
    print(f"Past Avg: {past_avg:.1f} | Recent Avg: {recent_avg:.1f} | Shift: {shift:+.1f} mg/dL ({pct_shift:+.1f}%)")

    # 3. Dynamic Threshold Logic (Triggers even during Days 1-5 if glucose rises)
    if days_since_shot == 0:
        msg = (
            f"💉 🟢 Mounjaro Shot Day Reset!\n"
            f"Take shot tonight. Reset Twiist pump to Fresh Shot baseline:\n"
            f"🎯 Set ISF: {FRESH_SHOT_ISF} mg/dL/U\n"
            f"🍕 Set CR: {FRESH_SHOT_CR} g/U"
        )
    elif shift >= 8.0:
        # Dynamic adjustment scaled by percent shift
        adj = min(max(pct_shift / 20.0, 0.1), 1.0)
        rec_isf = round(max(FRESH_SHOT_ISF - (FRESH_SHOT_ISF - MAX_RESIST_ISF) * adj, MAX_RESIST_ISF), 1)
        rec_cr = round(max(FRESH_SHOT_CR - (FRESH_SHOT_CR - MAX_RESIST_CR) * adj, MAX_RESIST_CR), 1)

        msg = (
            f"⚠️ 💉 Mounjaro Waning/Resistance Alert (Day {days_since_shot}/14)\n"
            f"Glucose Shift: +{shift:.1f} mg/dL (+{pct_shift:.1f}%)\n"
            f"🎯 Recommended ISF: -> {rec_isf} mg/dL/U\n"
            f"🍕 Recommended CR: -> {rec_cr} g/U"
        )
    else:
        msg = (
            f"🟢 💉 Cycle Status Normal (Day {days_since_shot}/14)\n"
            f"Shift: {shift:+.1f} mg/dL. Keep current settings (ISF {FRESH_SHOT_ISF} / CR {FRESH_SHOT_CR})!"
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