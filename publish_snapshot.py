#!/usr/bin/env python3
"""Publish a small recovery + today's-session snapshot for the 5:10am cloud brief.

The cloud sandbox that runs the brief cannot reach intervals.icu (egress proxy denies
it) but CAN reach raw.githubusercontent.com. GitHub Actions can reach both, so this
job is the bridge: it reads intervals.icu and writes wellness-latest.json here.

  ICU_KEY  ICU_ATHLETE   (from repository secrets)
"""
import base64, datetime, json, os, sys, urllib.error, urllib.request

# Health metrics are OFF by default: this repo is public. The brief gets recovery
# straight from Google Health instead. Set INCLUDE_HEALTH=1 only if that changes.
INCLUDE_HEALTH = os.environ.get("INCLUDE_HEALTH") == "1"
ICU = os.environ["ICU_KEY"]
AID = os.environ.get("ICU_ATHLETE", "i654192")
AUTH = base64.b64encode(f"API_KEY:{ICU}".encode()).decode()
HDR = {"Authorization": "Basic " + AUTH, "User-Agent": "claude-zwift-hrv/1.0"}

def get(path):
    req = urllib.request.Request(f"https://intervals.icu/api/v1/athlete/{AID}{path}", headers=HDR)
    return json.load(urllib.request.urlopen(req, timeout=40))

today = datetime.date.today()
start = today - datetime.timedelta(days=14)

import re
days = []
for w in get(f"/wellness?oldest={start}&newest={today}"):
    if not (w.get("hrv") or w.get("restingHR")):
        continue
    c = w.get("comments") or ""
    def grab(pat, cast=int):
        m = re.search(pat, c)
        return cast(m.group(1)) if m else None
    days.append({
        "date": w["id"],
        "hrv": w.get("hrv"),
        "restingHR": w.get("restingHR"),
        "sleepHours": round(w["sleepSecs"] / 3600, 1) if w.get("sleepSecs") else None,
        "respiration": w.get("respiration"),
        "spO2": w.get("spO2"),
        "avgSleepingHR": w.get("avgSleepingHR"),
        "deepSleepMin": grab(r"deep (\d+)m"),
        "remSleepMin": grab(r"REM (\d+)m"),
        "skinTempDev": grab(r"skinT ([+-][\d.]+)C", float),
        "ctl": round(w["ctl"], 1) if w.get("ctl") else None,
        "atl": round(w["atl"], 1) if w.get("atl") else None,
    })

ftp = None
for s in get("/sport-settings"):
    if "Ride" in (s.get("types") or []):
        ftp = s.get("ftp")

session = None
for e in get(f"/events?oldest={today}&newest={today}"):
    if e.get("category") == "WORKOUT":
        session = {"name": e.get("name"), "minutes": round((e.get("moving_time") or 0) / 60),
                   "tss": e.get("icu_training_load"), "workout": e.get("description")}

snap = {
    "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
    "for_date": today.isoformat(),
    "ftp_watts": ftp,
    "todays_ride": session,
    "baselines": {"hrv_mean": 41.0, "hrv_sd": 5.1, "restingHR_good_max": 50,
                  "restingHR_overreach_min": 51.5, "sleep_hours_mean": 7.9,
                  "deep_sleep_min_mean": 68},
    "days": days if INCLUDE_HEALTH else [],
    "health_included": INCLUDE_HEALTH,
    "health_note": ("Recovery metrics are deliberately not published here - this repo is "
                    "public. The morning brief reads them directly from the Google Health "
                    "API instead."),
}

out = "wellness-latest.json"
with open(out, "w") as f:
    json.dump(snap, f, indent=1)
print(f"wrote {out}: health_included={INCLUDE_HEALTH}, "
      f"days={len(snap['days'])}, ftp {ftp}, "
      f"ride {session['name'] if session else 'rest day'}")
