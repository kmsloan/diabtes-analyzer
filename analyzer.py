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
# PUMP SETTING BOUNDARIES (Pure Data-Driven Range)
# ============================================================================
# Protective floor (when daytime lows >= 4%)
SAFE_ISF = 36.0
SAFE_CR = 10.0
NIGHT_BASAL_SAFE = 0.85
DAY_BASAL_SAFE = 1.00

# Aggressive ceiling (when running high with minimal lows)
AGGRESSIVE_ISF = 26.0
AGGRESSIVE_CR = 6.0
NIGHT_BASAL_AGGR = 1.00
DAY_BASAL_AGGR = 1.50

# ============================================================================
# CLINICAL TARGETS
# ============================================================================
TARGET_MGDL = 115.0
HIGH_AVG_THRESHOLD = 140.0  # Tighten settings if 24h avg exceeds this
LOW_PCT_CAUTION = 4.0       # Nudge weaker if daytime lows >= 4%
LOW_PCT_DANGER = 8.0        # Full safety step-down if daytime lows >= 8%

def snap_basal(rate: float) -> float:
    return round(round(rate * 20) / 20.0, 2)

def get_tidepool_metrics():
    print("🔐 Logging into Tidepool API...")
    login_url = "https://api.tidepool.org/auth/login"
    res = requests.post(login_url, auth=(EMAIL, PASSWORD), timeout=10)
    if res.status_code != 200:
        raise Exception(f"Tidepool Login Failed ({res.status_code}): {res.text}")

    session_token = res.headers.get("x-tidepool-session-token")
    user_id = res.json().get("userid")
    headers = {"x-tidepool-session-token": session_token}

    # Fetch last 3 days of CGM, Bolus, and Basal data
    three_days_ago = (datetime.now(timezone.utc) - timedelta(days=3)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    data_url = f"https://api.tidepool.org/data/{user_id}?type=cbg,bolus,basal&startDate={three_days_ago}"
    data_res = requests.get(data_url, headers=headers, timeout=15)
    records = data_res.json() if data_res.status_code == 200 else []

    now_utc = datetime.now(timezone.utc)
    cutoff_24h = now_utc - timedelta(hours=24)

    readings_24h, daytime_24h = [], []
    total_insulin_3d = 0.0

    for item in records:
        dtype = item.get("type")
        t_str = item.get("time")
        if not t_str:
            continue
        t_dt = datetime.fromisoformat(t_str.replace("Z", "+00:00"))

        if dtype == "cbg" and "value" in item:
            val_mgdl = item["value"] * 18.0182 if item["value"] < 30 else item["value"]
            if t_dt >= cutoff_24h:
                readings_24h.append(val_mgdl)
                # Convert UTC to EST (UTC-5) for daytime filter (7 AM - 10 PM)
                local_hour = (t_dt - timedelta(hours=5)).hour
                if 7 <= local_hour < 22:
                    daytime_24h.append(val_mgdl)

        elif dtype == "bolus":
            total_insulin_3d += float(item.get("normal", 0.0))

        elif dtype == "basal":
            rate = float(item.get("rate", 0.0))
            dur_ms = float(item.get("duration", 0.0))
            total_insulin_3d += rate * (dur_ms / 3600000.0)

    avg_24h = sum(readings_24h) / len(readings_24h) if readings_24h else TARGET_MGDL
    day_low_pct = (sum(1 for v in daytime_24h if v < 70.0) / len(daytime_24h)) * 100.0 if daytime_24h else 0.0
    high_pct = (sum(1 for v in readings_24h if v > 180.0) / len(readings_24h)) * 100.0 if readings_24h else 0.0
    avg_tdd = total_insulin_3d / 3.0 if total_insulin_3d > 0 else 50.0

    return avg_24h, day_low_pct, high_pct, avg_tdd

def analyze():
    avg_24h, day_low_pct, high_pct, avg_tdd = get_tidepool_metrics()

    # Calculate biological baseline from actual 3-day Total Daily Dose (TDD)
    bio_cr = 500.0 / avg_tdd
    bio_isf = 1800.0 / avg_tdd

    # Start at neutral middle gear (0.5 -> CR 9 / ISF 32)
    gear = 0.5

    if day_low_pct >= LOW_PCT_DANGER:
        gear = 0.0
        status_note = f"🔴 High Daytime Lows ({day_low_pct:.1f}%). Stepping down to protective baseline."
    elif day_low_pct >= LOW_PCT_CAUTION:
        gear = 0.35
        status_note = f"🟡 Mild Daytime Lows ({day_low_pct:.1f}%). Holding moderate settings to prevent dips."
    elif avg_24h > HIGH_AVG_THRESHOLD or high_pct > 25.0:
        excess = min(max(avg_24h - HIGH_AVG_THRESHOLD, 0.0), 35.0)
        gear = min(0.65 + (excess / 35.0) * 0.35, 1.0)
        status_note = f"⚠️ Elevated glucose (Avg {avg_24h:.0f}, >180: {high_pct:.0f}%). Tightening profile."
    else:
        gear = 0.55
        status_note = f"🟢 In-range control (Avg {avg_24h:.0f} mg/dL). Holding steady sweet spot."

    rec_isf = int(round(SAFE_ISF - (SAFE_ISF - AGGRESSIVE_ISF) * gear))
    rec_cr = int(round(SAFE_CR - (SAFE_CR - AGGRESSIVE_CR) * gear))
    rec_night_basal = snap_basal(NIGHT_BASAL_SAFE + (NIGHT_BASAL_AGGR - NIGHT_BASAL_SAFE) * gear)
    rec_day_basal = snap_basal(DAY_BASAL_SAFE + (DAY_BASAL_AGGR - DAY_BASAL_SAFE) * gear)

    msg = (
        f"📊 24h Avg: {avg_24h:.0f} mg/dL | TDD: {avg_tdd:.1f}U\n"
        f"🧬 Bio Math: CR {bio_cr:.1f} / ISF {bio_isf:.0f}\n"
        f"{status_note}\n"
        f"🎯 Set ISF: {rec_isf} mg/dL/U\n"
        f"🍕 Set CR: {rec_cr} g/U\n"
        f"🌙 Night Basal: {rec_night_basal:.2f} U/hr\n"
        f"☀️ Day Basal: {rec_day_basal:.2f} U/hr"
    )

    try:
        res = requests.post(
            f"https://ntfy.sh/{NTFY_TOPIC}",
            data=msg.encode("utf-8"),
            headers={"Title": "Twiist Data-Driven Update"}
        )
        if res.status_code == 200:
            print("📲 Push notification sent successfully!")
            print(f"\n--- Preview ---\n{msg}\n---------------")
    except Exception as e:
        print(f"❌ Error: {e}")

if __name__ == "__main__":
    analyze()