import base64
import json
import urllib.request
from datetime import datetime, timedelta, timezone

# ==========================================
# 🛠️ USER CONFIGURATION
# ==========================================
TIDEPOOL_EMAIL = "kmsloan4@gmail.com"
TIDEPOOL_PASSWORD = "Number4444!!"

# 📱 Enter your custom ntfy topic name here:
NTFY_TOPIC = "kaitlin-twiist-alerts"

# ⚙️ Your actual Twiist baseline settings:
DEFAULT_ISF = 22.0
DEFAULT_CR = 6.0


def send_phone_alert(message, title="Mounjaro Resistance Alert"):
    """Sends a push notification directly to your phone via ntfy."""
    try:
        encoded_title = title.encode('utf-8').decode('latin-1')
        req = urllib.request.Request(
            f"https://ntfy.sh/{NTFY_TOPIC}",
            data=message.encode('utf-8'),
            headers={
                "Title": encoded_title,
                "Priority": "high",
                "Tags": "warning,syringe"
            },
            method="POST"
        )
        with urllib.request.urlopen(req) as response:
            print("📲 Push notification sent to phone!")
    except Exception as e:
        print(f"❌ Failed to send phone alert: {e}")


def get_tidepool_session():
    """Logs into Tidepool API using HTTP Basic Authentication."""
    login_url = "https://api.tidepool.org/auth/login"
    auth_str = f"{TIDEPOOL_EMAIL}:{TIDEPOOL_PASSWORD}"
    b64_auth = base64.b64encode(auth_str.encode('utf-8')).decode('utf-8')
    
    req = urllib.request.Request(
        login_url,
        headers={
            "Authorization": f"Basic {b64_auth}",
            "Accept": "application/json"
        },
        method="POST"
    )
    try:
        print("🔐 Logging into Tidepool...")
        with urllib.request.urlopen(req) as response:
            token = response.headers.get("x-tidepool-session-token")
            data = json.loads(response.read().decode('utf-8'))
            return token, data.get("userid")
    except Exception as e:
        print(f"❌ Login failed: {e}")
        return None, None


def fetch_cgm_data(token, user_id, days=14):
    """Pulls recent CGM glucose entries."""
    start_time = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat().replace('+00:00', 'Z')
    data_url = f"https://api.tidepool.org/data/{user_id}?type=cbg&startDate={start_time}"
    
    req = urllib.request.Request(data_url)
    req.add_header("x-tidepool-session-token", token)
    
    print(f"📥 Pulling last {days} days of Libre 3 data...")
    try:
        with urllib.request.urlopen(req) as response:
            if response.status == 200:
                return json.loads(response.read().decode('utf-8'))
    except Exception as e:
        print(f"❌ Error fetching CGM data: {e}")
        return None


def analyze_and_calculate_settings(cgm_data):
    """Calculates sensitivity shift and sends push notification if needed."""
    now = datetime.now(timezone.utc)
    seven_days_ago = now - timedelta(days=7)
    fourteen_days_ago = now - timedelta(days=14)

    week_1_readings = []
    week_2_readings = []

    for entry in cgm_data:
        if entry.get('type') == 'cbg' and 'value' in entry and 'time' in entry:
            try:
                val = float(entry['value'])
                if val < 30:  # Convert mmol/L to mg/dL if needed
                    val = val * 18.0155

                entry_time = datetime.fromisoformat(entry['time'].replace('Z', '+00:00'))

                if fourteen_days_ago <= entry_time < seven_days_ago:
                    week_1_readings.append(val)
                elif entry_time >= seven_days_ago:
                    week_2_readings.append(val)
            except ValueError:
                continue

    if not week_1_readings or not week_2_readings:
        print("⚠️ Not enough CGM data points found for both weeks.")
        return

    avg_week_1 = sum(week_1_readings) / len(week_1_readings)
    avg_week_2 = sum(week_2_readings) / len(week_2_readings)

    diff = avg_week_2 - avg_week_1
    percent_increase = (diff / avg_week_1) * 100

    print("\n==============================================")
    print("📊 MOUNJARO CYCLE REPORT")
    print("==============================================")
    print(f"🔹 Week 1 Avg Glucose: {avg_week_1:.1f} mg/dL")
    print(f"🔹 Week 2 Avg Glucose: {avg_week_2:.1f} mg/dL")
    print(f"📈 Shift: {diff:+.1f} mg/dL ({percent_increase:+.1f}%)")
    print("----------------------------------------------")

    if diff >= 10:
        shift_factor = 1 + (percent_increase / 100)
        new_isf = round(DEFAULT_ISF / shift_factor, 1)
        new_cr = round(DEFAULT_CR / shift_factor, 1)

        print("⚠️ RESISTANCE ALERT: Increased resistance detected!")
        print(f"🎯 Recommended ISF: Change {DEFAULT_ISF} ➔ {new_isf} mg/dL/U")
        print(f"🍕 Recommended CR:  Change {DEFAULT_CR} ➔ {new_cr} g/U")

        alert_body = (
            f"Shift: {diff:+.1f} mg/dL ({percent_increase:+.1f}%)\n"
            f"🎯 Change ISF: {DEFAULT_ISF} -> {new_isf} mg/dL/U\n"
            f"🍕 Change CR: {DEFAULT_CR} -> {new_cr} g/U"
        )
        send_phone_alert(alert_body, title="Mounjaro Resistance Alert")
    else:
        print("🟢 SENSITIVITY NORMAL: Maintain standard settings.")
        send_phone_alert("Glucose shift is normal (+0-9 mg/dL). Keep standard settings!", title="Sensitivity Normal")

    print("==============================================\n")


if __name__ == "__main__":
    token, user_id = get_tidepool_session()
    if token and user_id:
        cgm_data = fetch_cgm_data(token, user_id, days=14)
        if cgm_data:
            analyze_and_calculate_settings(cgm_data)