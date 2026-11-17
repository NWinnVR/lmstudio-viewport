← [README](../README.md) · [Wiki index](index.md)

# API reference

| Method | Path | Returns |
|---|---|---|
| GET | `/api/status` | server + model + GPU + RAM + live needles + version |
| GET | `/api/events?n=200` | recent generation events |
| GET | `/api/series?metric=tps&range=15m` | time-bucketed series (1m/5m/15m/1h/1d/7d/1mo/all) |
| GET | `/api/lifetime?days=30` | lifetime rollup |
| GET | `/api/history?days=7` | per-day rollup |
| GET | `/api/totals` | all-time tokens + kWh/$ + active time + $ saved |
| GET | `/api/settings` | current knobs |
| POST | `/api/settings` | update a knob |
| POST | `/api/compact` | rotate old samples to `archive/` |
| POST | `/api/exit` | close the viewport |
