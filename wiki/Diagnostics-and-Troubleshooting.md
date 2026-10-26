# Diagnostics and Troubleshooting

## Download diagnostics — attach it to every issue

**Settings → Devices & services → Felicity → ⋮ → Download diagnostics.**

The file contains a read-only dump of the inverter's registers, read through the integration's own connection (Home Assistant keeps running):

- the **raw register words** and the decoded values for the model's full map (on TREX-5/6/10, IVGM and IVAM also every other address the model's protocol document defines);
- **physics checks** — power compared with voltage × current, and registers that report the same quantity compared with each other. `SCALE SUSPECT` means a scale in the register map is probably wrong;
- the decoded economic-rule windows;
- **poll statistics** — how long a poll takes, how many reads answered or timed out, hidden retries, registers dropped as silent.

The dump has a 25-second time budget, so it also finishes on a slow or flaky link (what could not be read in time is listed in the file). A photo of the inverter's display taken at the same moment helps to confirm values.

**Without Home Assistant**: `tools/ivgm_dump.py` in the repository produces the same dump:

```
python tools/ivgm_dump.py --host 192.168.1.50              # Modbus TCP
python tools/ivgm_dump.py --host 192.168.1.50 --framer rtu # RTU tunnelled over TCP (gateways)
python tools/ivgm_dump.py --serial /dev/ttyUSB0            # USB-RS485 (Windows: COM3)
```

## Slow or unreliable RS485 links

The TREX-5/6/10K runs at **2400 baud**. A full poll is a handful of large reads; on a healthy link that takes a few seconds.

Signs of a link problem: a poll taking minutes, `Read error … No response received after 3 retries` in the log, sensors flipping to `unknown`. pymodbus retries silently and waits its full timeout (~5 s) per retry, so a link that drops some replies adds 5-second steps to every poll before anything is logged. The integration warns once an hour when a poll takes more than twice the update interval.

What the integration does about it:
- a read that gets no answer is split into smaller reads on the next poll; a link that answers short reads settles there;
- a register that has answered once is never dropped; only an address that never answers is (one warning, until reload);
- after 3 silent reads in a row it pauses polling (30 s, doubling to 5 min) instead of hanging Home Assistant's startup.

What usually fixes it: RS485 wiring and termination, and the gateway settings. See [Waveshare RS485 TO POE ETH (B)](Waveshare-RS485_TO_POE_ETH_%28B%29) for settings that work in the field.

## What the EMS card is telling you

| You see | Meaning |
|---|---|
| Amber chip **Consumption profile flat / partial** | Less than 7 days of hourly history for your load meter (new install, or the recorder misses hours). The plan uses a flat daily average where history is missing. Picking a real energy meter as *consumption override entity* gives the best profile. |
| Banner **Rule 1 window** warning | A planned action falls outside the inverter's Rule 1 time window or weekdays. Set *Rule 1 Time Window* / *Rule 1 Weekday* to `auto`, or widen the window yourself. |
| Reason *"Battery runs empty at … — buying those kWh live is cheaper"* | The planner checked charging earlier and it costs more after losses than buying at that moment. |
| **Grid charge limit** / **Rule 1 power** chip below your Power Level, chip red | Safe Power has cut the battery because a phase is over your limit. It returns to the Power Level when that fits again. |
| Reason *"… stores cheap energy for the coming days (this week's average …)"* | Today or tomorrow is clearly cheaper than this week's average: the battery is filled for the days after (see [Energy Management Basics](Energy-Management-Basics)). |
| Engine chip **Greedy (fallback)** / **Optimal (fallback)** | You selected MILP but no solver could run — see *MILP will not load* below. |
| THRESHOLD figure that does not change | Known issue: in auto mode the threshold shows the dearest price of the last plan that bought something. |

## "Why didn't it charge?" checklist

1. **Grid Mode** is not `off` and **Price Mode** is `auto`.
2. The reason line on the card — the planner says why it did or did not buy.
3. **Consumption profile** chip — a wrong profile gives a wrong plan.
4. **Rule 1 window** banner — the inverter ignores a rule outside its window.
5. History of `energy_state` and the **Rule 1 Power** entity: was it charging, and at what power? A low Rule 1 power means Safe Power cut it (see [Power Management Basics](Power-Management-Basics)).
6. Is your installed version the latest release? HACS installs releases (tags), not the newest code on GitHub.
7. `schedule_status` → `input_sources.price_reference`: this week's average price. Empty for the first 3 days after an update unless HA's statistics of your price entity could rebuild it — until then a cheap day only buys for the night.

## MILP will not load

The default engine (**Optimal**) needs nothing extra and is the better one for From Grid (Save Money, Self-Sufficiency, Battery Care): it is exact, and it uses this week's prices, which MILP does not. MILP is worth choosing for **Trader** (To Grid / Both).

MILP needs an LP solver program. On **Home Assistant OS** pulp's bundled CBC cannot run: it is built for glibc, and the Home Assistant container is Alpine (musl) — the file is there, starting it fails with *"No such file or directory"*. `pulp[cbc]` installs the same kind of build. **HiGHS** (`highspy`) has musl wheels and runs inside Python.

1. Select **MILP** as the scheduler engine and **restart Home Assistant once**. While the engine is MILP, the integration installs HiGHS at startup through Home Assistant's own installer — again after every Home Assistant update, which replaces the container and drops hand-installed packages. A failed install only logs a warning; the exact planner then runs.
2. Check: hover the engine chip on the card, or `schedule_status` → `milp_status`: `state: active`, `solver: HiGHS`. A broken bundled CBC shows in `tried` as `PULP_CBC_CMD(broken: …)` and is skipped.
3. Still not running? The log line *"Could not install the HiGHS solver"* says why (e.g. no wheel for a 32-bit ARM board). By hand, from the *Advanced SSH & Web Terminal* add-on (protection mode off): `docker exec -it homeassistant pip install highspy`, then restart. `tools/check_milp.py` (copy it to `/config`, run `docker exec -it homeassistant python3 /config/check_milp.py`) prints a verdict.

## Energy statistics jumped

Until October 2026 one wrong reading of an energy counter (the inverter's day-rollover glitch, or a Modbus reply matched to the wrong request) could add the counter's whole value to Home Assistant's long-term statistics. Since then impossible readings never reach the sensors. Statistics that were inflated before need a one-time correction in **Developer tools → Statistics**.
