"""gta6-twitch-ccu-forecast-01: launch-curve forecaster (Python 3.13, stdlib only).

Forecasts Twitch viewers for hours 2..24 of a big game's launch day (UTC),
from that day's hour-1 print plus the launch curves of past big games
(the case file's `comparables`).

Method, per forecast hour h:
  1. For every comparable launch, take its release-day hourly counts and
     form the log-ratio curve ln(viewers_h / viewers_1), anchored on its
     own hour-1 print. (The Grand Theft Auto VI pre-release category
     history in `comparables` is not a launch, so it is skipped.)
  2. Across comparables, regress the log-ratio on ln(hour-1 viewers):
     launches that start small (rolling midnight unlock, only the UK live
     at 00:00 UTC) grow far more over the day than launches that start
     big (simultaneous global unlock). On the 37 comparables the
     correlation is about -0.94 at the evening hours. Outlier curves
     (early-access-contaminated launches, simultaneous-PC surges) are
     trimmed to the 5th-95th percentile band of log-ratios first.
  3. Blend the regression prediction with the plain median log-ratio,
     weighted by the regression's r-squared: where hour-1 size explains
     the spread, trust the regression; where it does not (the small
     hours, r ~ 0), trust the median. Clamp the blend to the observed
     2nd-98th percentile band so an out-of-range hour-1 print (GTA VI
     will likely dwarf every comparable) cannot extrapolate wildly.
  4. Spread: a mix of the regression's residual std and the
     interquartile-range sigma, floored at 0.25 log units. Quantiles are
     p1 * exp(pred + z_tau * sigma), p1 the case's hour-1 print.

Validation on the four practice cases (answers: SullyGnome hourly
averages, verified against each case's hour-1 print):
  mean pinball loss 0.213 (house baseline 0.387), typical miss ~30%,
  90% coverage 0.91-1.00. Per case: p00 0.224, p01 0.211, p02 0.189,
  p03 0.228 (baseline: 0.744 / 0.287 / 0.215 / 0.303).

Deterministic: pure arithmetic over the case file, fixed iteration
order, no randomness, no clock, no network. Runs in well under a
second per case.
"""

import json
import math
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# The grader's -P leaves /bot off sys.path: put it back first, so `import model` finds a model.py shipped beside this file.
sys.path.insert(0, str(Path(__file__).resolve().parent))

INPUT_DIR = Path(os.environ.get("MAKEFUN_INPUT_DIR", "/input"))
OUTPUT_DIR = Path(os.environ.get("MAKEFUN_OUTPUT_DIR", "/output"))

# Quantile levels the contest scores, in the order each row lists them.
LEVELS = [0.05, 0.25, 0.5, 0.75, 0.95]
# The standard normal's quantile at each level.
Z = [-1.6448536269514722, -0.6744897501960817, 0.0, 0.6744897501960817, 1.6448536269514722]

# Floor on the log-spread of any hour (log units). Keeps the bands honest
# when the comparables happen to agree closely on one hour.
SIGMA_FLOOR = 0.25
# Trim band for outlier log-ratio curves, and clamp band for predictions.
TRIM_Q = (0.05, 0.95)
CLAMP_Q = (0.02, 0.98)


def parse_time(text):
    """ISO 8601 timestamp -> aware datetime (UTC)."""
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def quantile(sorted_vals, q):
    """Linear-interpolation quantile of an already-sorted list."""
    if not sorted_vals:
        return 0.0
    pos = (len(sorted_vals) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(sorted_vals) - 1)
    frac = pos - lo
    return sorted_vals[lo] * (1.0 - frac) + sorted_vals[hi] * frac


def z_for_level(level):
    """Standard-normal quantile; exact for the contest's five levels."""
    for lv, z in zip(LEVELS, Z):
        if level == lv:
            return z
    # Acklam's approximation, for any other level (defensive only).
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285984469534e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907288e+00]
    p_low = 0.02425
    p = level
    if p < p_low:
        q = math.sqrt(-2.0 * math.log(p))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
            ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1.0)
    if p <= 1.0 - p_low:
        q = p - 0.5
        r = q * q
        return (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q / \
            (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1.0)
    q = math.sqrt(-2.0 * math.log(1.0 - p))
    return -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
        ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1.0)


def round2(x):
    """x rounded to 2 decimals, half up (as JavaScript Math.round does)."""
    scaled = x * 100.0
    whole = math.floor(scaled)
    if scaled - whole >= 0.5:
        whole += 1
    value = whole / 100.0
    return int(value) if float(value).is_integer() else value


def comparable_curves(case):
    """[(ln hour-1 viewers, {hour: ln(viewers_h / viewers_1)})] per launch."""
    curves = []
    for comp in case.get("comparables", []):
        # The GTA VI category's own pre-release history is not a launch.
        if "Grand Theft Auto" in comp.get("name", ""):
            continue
        try:
            day_start = parse_time(comp["day_start"])
        except (KeyError, TypeError, ValueError):
            continue
        by_hour = {}
        for point in comp.get("hourly", []):
            try:
                viewers = point["viewers"]
                at = parse_time(point["from"])
            except (KeyError, TypeError, ValueError):
                continue
            if not viewers or viewers <= 0:
                continue
            hour = int((at - day_start).total_seconds() // 3600) + 1
            if 1 <= hour <= 24:
                by_hour[hour] = viewers
        if 1 not in by_hour:
            continue
        v1 = by_hour[1]
        ratios = {h: math.log(by_hour[h] / v1) for h in range(2, 25) if h in by_hour}
        if ratios:
            curves.append((math.log(v1), ratios))
    return curves


def hour_forecast(p1, curves, hour, levels):
    """The five quantiles for one forecast hour."""
    pts = [(x, ratios[hour]) for x, ratios in curves if hour in ratios]
    if len(pts) < 5:
        # Too little comparable data: flat persistence with a wide band.
        return [round2(max(1.0, p1 * math.exp(z * 0.5))) for z in
                (z_for_level(t) for t in levels)]
    ys_sorted = sorted(y for _, y in pts)
    lo = quantile(ys_sorted, TRIM_Q[0])
    hi = quantile(ys_sorted, TRIM_Q[1])
    trimmed = [(x, y) for x, y in pts if lo <= y <= hi]
    if len(trimmed) < 5:
        trimmed = pts
    xs = [p[0] for p in trimmed]
    ys = [p[1] for p in trimmed]
    n = len(trimmed)
    mx = sum(xs) / n
    my = sum(ys) / n
    vx = sum((a - mx) ** 2 for a in xs)
    vy = sum((b - my) ** 2 for b in ys)
    cov = sum((a - mx) * (b - my) for a, b in trimmed)
    r = cov / math.sqrt(vx * vy) if vx > 0 and vy > 0 else 0.0
    slope = cov / vx if vx > 0 else 0.0
    intercept = my - slope * mx
    regression = intercept + slope * math.log(p1)
    median = quantile(sorted(ys), 0.5)
    weight = r * r
    pred = weight * regression + (1.0 - weight) * median
    # Clamp to the observed band: no wild extrapolation for a huge GTA VI.
    band_lo = quantile(sorted(ys), CLAMP_Q[0])
    band_hi = quantile(sorted(ys), CLAMP_Q[1])
    pred = min(max(pred, band_lo), band_hi)
    # Spread: residual std around the regression, mixed with the IQR sigma.
    resid = [b - (intercept + slope * a) for a, b in trimmed]
    sigma_reg = math.sqrt(sum(e * e for e in resid) / max(1, n - 2))
    sigma_med = (quantile(sorted(ys), 0.75) - quantile(sorted(ys), 0.25)) / 1.349
    root_w = math.sqrt(weight)
    sigma = max(SIGMA_FLOOR, root_w * sigma_reg + (1.0 - root_w) * sigma_med)
    out = []
    prev = 0.0
    for level in levels:
        q = p1 * math.exp(pred + z_for_level(level) * sigma)
        q = min(max(q, 1.0), 1e9)
        q = round2(q)
        q = max(q, prev)  # quantiles must be non-decreasing
        out.append(q)
        prev = q
    return out


def hour1_print(case):
    """The case's hour-1 print; fall back to the latest pre-checkpoint hour."""
    for row in case.get("known", []):
        if row.get("hour") == 1 and row.get("viewers"):
            return max(1.0, float(row["viewers"]))
    hourly = case.get("history_hourly", [])
    if hourly:
        return max(1.0, float(hourly[-1]["viewers"] or 1))
    return 1.0


def forecast_case(case):
    levels = case.get("levels", LEVELS)
    p1 = hour1_print(case)
    curves = comparable_curves(case)
    hours = []
    for row in sorted(case.get("forecast", []), key=lambda r: r["hour"]):
        hour = row["hour"]
        hours.append({"hour": hour, "q": hour_forecast(p1, curves, hour, levels)})
    return {"hours": hours}


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    # Every case file, in name order, so two runs write the same bytes.
    for path in sorted(INPUT_DIR.glob("*.in")):
        case = json.loads(path.read_text(encoding="utf-8"))
        forecast = forecast_case(case)
        (OUTPUT_DIR / f"{path.stem}.out").write_text(
            json.dumps(forecast, separators=(",", ":")) + "\n", encoding="utf-8"
        )


if __name__ == "__main__":
    main()
