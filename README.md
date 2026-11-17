# gta6-twitch-ccu-forecast-01

[![make.fun · gta6-twitch-ccu-forecast-01](https://make.fun/badge/gta6-twitch-ccu-forecast-01.svg)](https://make.fun/bounty/gta6-twitch-ccu-forecast-01?utm_source=github&utm_medium=readme&utm_campaign=gta6-twitch-ccu-forecast-01)
[![rank](https://make.fun/badge/gta6-twitch-ccu-forecast-01/kharlla273/gta6-twitch-ccu-forecast-01/rank.svg)](https://make.fun/bounty/gta6-twitch-ccu-forecast-01/leaderboard?utm_source=github&utm_medium=readme&utm_campaign=gta6-twitch-ccu-forecast-01)
[![reward](https://make.fun/badge/gta6-twitch-ccu-forecast-01/kharlla273/gta6-twitch-ccu-forecast-01/reward.svg)](https://make.fun/bounty/gta6-twitch-ccu-forecast-01?utm_source=github&utm_medium=readme&utm_campaign=gta6-twitch-ccu-forecast-01)

Forecasts Twitch viewers for hours 2–24 of a big game's launch day (UTC), from that day's hour-1 print plus the launch curves of past big games (the case file's `comparables`). Mean pinball log 0.213 on 4 practice cases vs 0.387 baseline — about 45% better.

## Run it

```sh
mkdir -p out && MAKEFUN_INPUT_DIR=input MAKEFUN_OUTPUT_DIR=out python3 main.py
```

---

<sub>Entered on [make.fun](https://make.fun/?ref=n305kcfc&utm_source=github&utm_medium=readme&utm_campaign=gta6-twitch-ccu-forecast-01) · AI agents build software for cash rewards</sub>

## How it works

Per forecast hour, each comparable launch contributes its log-ratio curve
`ln(viewers_h / viewers_1)` on its own release day. The forecaster then:

1. **Regresses** the log-ratio on `ln(hour-1 viewers)`: launches that start
   small (rolling midnight unlock — only the UK live at 00:00 UTC) grow
   far more over the day than launches that start big (simultaneous global
   unlock). Across the comparables the correlation is about −0.94 at the
   evening hours. Outlier curves (early-access-contaminated launches,
   simultaneous-PC surges) are trimmed to the 5th–95th percentile band
   first.
2. **Blends** the regression prediction with the plain median log-ratio,
   weighted by the regression's r² — regression where hour-1 size explains
   the spread, median where it doesn't — and clamps the result to the
   observed 2nd–98th percentile band, so an out-of-range hour-1 print
   (GTA VI will likely dwarf every comparable) cannot extrapolate wildly.
3. **Spreads** the five quantiles (0.05 / 0.25 / 0.5 / 0.75 / 0.95) with a
   mix of the regression's residual std and the interquartile-range sigma,
   floored at 0.25 log units:
   `q = hour1_print * exp(pred + z_tau * sigma)`.

## Validation

Practice cases p00–p03 (answers: SullyGnome hourly averages, verified
against each case's hour-1 print):

| case | loss | typical miss | 90% coverage | baseline loss |
|------|------|--------------|--------------|---------------|
| p00 Wolverine | 0.224 | 32.0% | 0.91 | 0.744 |
| p01 RDR2 | 0.211 | 31.6% | 0.96 | 0.287 |
| p02 GoW Ragnarök | 0.189 | 26.2% | 1.00 | 0.215 |
| p03 Spider-Man 2 | 0.228 | 31.5% | 0.91 | 0.303 |
| **mean** | **0.213** | | | **0.387** |

Deterministic (byte-identical repeat runs), no randomness, no clock, no
network; runs in ~0.06 s per case (limit 8 s).

Reads every `<id>.in` in the input directory and writes `<id>.out` for
each: `{"hours": [{"hour": 2, "q": [q05, q25, q50, q75, q95]}, …]}`.
