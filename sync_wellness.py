#!/usr/bin/env python3
"""Google Health (Fitbit) -> intervals.icu wellness.

Runs in GitHub Actions. All credentials come from environment variables, which are
supplied by GitHub Secrets. Nothing sensitive lives in this repository.

  GOOGLE_CLIENT_ID  GOOGLE_CLIENT_SECRET  GOOGLE_REFRESH_TOKEN  ICU_KEY  ICU_ATHLETE

Writes are idempotent: re-running simply overwrites the same days, so a wide window
costs nothing and heals any gap left by a missed run.
"""
import datetime, json, os, sys, urllib.error, urllib.parse, urllib.request

CID  = os.environ["GOOGLE_CLIENT_ID"]
CS   = os.environ["GOOGLE_CLIENT_SECRET"]
RT   = os.environ["GOOGLE_REFRESH_TOKEN"]
ICU  = os.environ["ICU_KEY"]
AID  = os.environ.get("ICU_ATHLETE", "i654192")
DAYS = int(os.environ.get("DAYS", "14"))

def post(url, data, headers=None):
    req = urllib.request.Request(url, data=urllib.parse.urlencode(data).encode(),
                                 headers=headers or {})
    return json.load(urllib.request.urlopen(req, timeout=40))

try:
    AT = post("https://oauth2.googleapis.com/token",
              {"client_id": CID, "client_secret": CS,
               "refresh_token": RT, "grant_type": "refresh_token"})["access_token"]
except urllib.error.HTTPError as e:
    sys.exit(f"Google token refresh failed: HTTP {e.code} {e.read().decode()[:300]}\n"
             "If this is invalid_grant, the refresh token was revoked or expired - "
             "re-run gh_auth.py locally and update the GOOGLE_REFRESH_TOKEN secret.")

end   = datetime.date.today() + datetime.timedelta(days=1)
start = end - datetime.timedelta(days=DAYS)

def health(dtype, filt):
    pts, tok = [], None
    while True:
        q = {"pageSize": 300, "filter": filt}
        if tok: q["pageToken"] = tok
        req = urllib.request.Request(
            f"https://health.googleapis.com/v4/users/me/dataTypes/{dtype}/dataPoints"
            f"?{urllib.parse.urlencode(q)}", headers={"Authorization": f"Bearer {AT}"})
        try:
            r = json.load(urllib.request.urlopen(req, timeout=40))
        except urllib.error.HTTPError as e:
            print(f"  {dtype}: HTTP {e.code} {e.read().decode()[:160]}"); return pts
        pts += r.get("dataPoints", [])
        tok = r.get("nextPageToken")
        if not tok: return pts

d2s = lambda d: f"{d['year']:04d}-{d['month']:02d}-{d['day']:02d}"
days = {}

for p in health("daily-heart-rate-variability",
                f'daily_heart_rate_variability.date >= "{start}" AND '
                f'daily_heart_rate_variability.date < "{end}"'):
    v = p.get("dailyHeartRateVariability") or {}
    # The Fitbit app displays averageHeartRateVariabilityMilliseconds, NOT the deep-sleep
    # RMSSD (which runs ~5 ms lower). Verified against the app 2026-09-02.
    if v.get("date") and v.get("averageHeartRateVariabilityMilliseconds") is not None:
        days.setdefault(d2s(v["date"]), {})["hrv"] = round(
            float(v["averageHeartRateVariabilityMilliseconds"]), 1)
    if v.get("date") and v.get("nonRemHeartRateBeatsPerMinute"):
        days.setdefault(d2s(v["date"]), {})["avgSleepingHR"] = int(
            float(v["nonRemHeartRateBeatsPerMinute"]))

for p in health("daily-resting-heart-rate",
                f'daily_resting_heart_rate.date >= "{start}" AND '
                f'daily_resting_heart_rate.date < "{end}"'):
    v = p.get("dailyRestingHeartRate") or {}
    if v.get("date") and v.get("beatsPerMinute"):
        days.setdefault(d2s(v["date"]), {})["restingHR"] = int(float(v["beatsPerMinute"]))

# --- respiratory rate: often the earliest illness signal, moves before HRV ---
for p in health("daily-respiratory-rate",
                f'daily_respiratory_rate.date >= "{start}" AND '
                f'daily_respiratory_rate.date < "{end}"'):
    v = p.get("dailyRespiratoryRate") or {}
    if v.get("date") and v.get("breathsPerMinute") is not None:
        days.setdefault(d2s(v["date"]), {})["respiration"] = round(float(v["breathsPerMinute"]), 1)

# --- blood oxygen ---
for p in health("daily-oxygen-saturation",
                f'daily_oxygen_saturation.date >= "{start}" AND '
                f'daily_oxygen_saturation.date < "{end}"'):
    v = p.get("dailyOxygenSaturation") or {}
    if v.get("date") and v.get("averagePercentage") is not None:
        days.setdefault(d2s(v["date"]), {})["spO2"] = round(float(v["averagePercentage"]), 1)

# --- skin temperature deviation from personal baseline. With respiratory rate this is
#     the classic early-illness pair; intervals.icu has no field, so it goes in comments ---
for p in health("daily-sleep-temperature-derivations",
                f'daily_sleep_temperature_derivations.date >= "{start}" AND '
                f'daily_sleep_temperature_derivations.date < "{end}"'):
    v = p.get("dailySleepTemperatureDerivations") or {}
    if not v.get("date"): continue
    rel = v.get("relativeTemperatureCelsius")
    if rel is None and v.get("nightlyTemperatureCelsius") is not None \
       and v.get("baselineTemperatureCelsius") is not None:
        rel = v["nightlyTemperatureCelsius"] - v["baselineTemperatureCelsius"]
    if rel is not None:
        days.setdefault(d2s(v["date"]), {})["_skin"] = round(float(rel), 2)

for p in health("sleep", f'sleep.interval.civil_end_time >= "{start}" AND '
                         f'sleep.interval.civil_end_time < "{end}"'):
    v  = p.get("sleep") or {}
    if not (v.get("metadata") or {}).get("mainSleep"): continue
    iv = v.get("interval") or {}
    m  = (v.get("summary") or {}).get("minutesAsleep")
    if not m or not iv.get("endTime"): continue
    # civilEndTime comes back null; derive the local date from endTime + endUtcOffset
    dt = datetime.datetime.fromisoformat(iv["endTime"].replace("Z", "+00:00"))
    dt += datetime.timedelta(seconds=int(str(iv.get("endUtcOffset", "0s")).rstrip("s")))
    key = dt.date().isoformat()
    days.setdefault(key, {})["sleepSecs"] = int(float(m) * 60)
    stages = {x.get("type"): int(x.get("minutes", 0))
              for x in ((v.get("summary") or {}).get("stagesSummary") or [])}
    if stages:
        days[key]["_stages"] = stages
    lat = (v.get("summary") or {}).get("minutesToFallAsleep")
    if lat is not None:
        days[key]["_latency"] = int(float(lat))

if not days:
    sys.exit("No wellness data returned - check scopes and that Fitbit is syncing.")

import base64
auth = base64.b64encode(f"API_KEY:{ICU}".encode()).decode()
written = 0
for d in sorted(days):
    v = days[d]
    bits = []
    st = v.pop("_stages", None)
    if st:
        bits.append("deep " + str(st.get("DEEP", 0)) + "m")
        bits.append("REM " + str(st.get("REM", 0)) + "m")
        bits.append("light " + str(st.get("LIGHT", 0)) + "m")
        if st.get("AWAKE"): bits.append("awake " + str(st["AWAKE"]) + "m")
    lat = v.pop("_latency", None)
    if lat is not None: bits.append("latency " + str(lat) + "m")
    skin = v.pop("_skin", None)
    if skin is not None: bits.append("skinT " + ("+" if skin >= 0 else "") + str(skin) + "C")
    if bits:
        v["comments"] = " | ".join(bits)
    body = json.dumps({"id": d, **v}).encode()
    req = urllib.request.Request(
        f"https://intervals.icu/api/v1/athlete/{AID}/wellness/{d}", data=body, method="PUT",
        headers={"Authorization": "Basic " + auth, "Content-Type": "application/json",
                 # intervals.icu rejects urllib's default User-Agent with a bare 403
                 "User-Agent": "claude-zwift-hrv/1.0"})
    try:
        r = json.load(urllib.request.urlopen(req, timeout=40))
        written += 1
        print(f"  {d}  hrv {r.get('hrv')}  rhr {r.get('restingHR')}  "
              f"sleep {round(r['sleepSecs']/3600,1) if r.get('sleepSecs') else '-'}  "
              f"RR {r.get('respiration')}  spO2 {r.get('spO2')}  | {r.get('comments','')}")
    except urllib.error.HTTPError as e:
        print(f"  {d}  intervals.icu HTTP {e.code}")

print(f"\n{written}/{len(days)} days written to intervals.icu")
