# Maui stations

Maui stations run one of the Maui editions; see `docs/editions.md` for
packages, images, firmware and offline updates.

| | Maui v1.1 | Maui v1.2 |
|---|---|---|
| MCU | TI MSPM0 behind an FTDI bridge | ITE IT82002, native USB CDC-ACM (18d1:5085) |
| UI | `/maui/v1.1/*` (legacy URLs redirect) | `/maui/v1.2/testing`, `/programming`, `/serial` |
| Firmware components | `mcu`, `pdc` | `mcu_app`, `mcu_full`, `pdc` (+ H2H, provided by the operator) |
| Results | `/var/log/diagnoseme/maui_v11_results` | `/var/log/diagnoseme/maui_v12_results` |

## Station overrides

Set these in `/etc/diagnoseme/config.local.env` (never in `config.env`, which
is replaced on upgrade).

| Variable | Editions | Meaning |
|----------|----------|---------|
| `STATION_ID`, `RESULTS_SERVER` | all Maui | Station id in the result records, optional upload endpoint |
| `MAUI_ADB_SIZE_MB` | all Maui | Size of the first random ADB test file (default 400) |
| `MAUI_ADB_DEADLINE_S` | all Maui | ADB transfer deadline (default 40 s) |
| `MAUI_ADB_MIN_RATE_MB_S` | all Maui | Minimum push/pull rate (default 0 = only logged) |
| `MAUI_ADB_MIN_LINK_MBPS` | all Maui | Required H2H link speed on host and DUT (default 5000, 0 = not checked) |
| `MAUI_V12_APPROVED_FW` | v1.2 | Extra approved MCU App builds (`<major>.<count>-<hash>`, comma separated, or `any`) |
| `MAUI_V12_HWREVS` | v1.2 | Accepted `hwrev` values (default 3) |
| `MAUI_ITE_NO_OUTPUT_TIMEOUT_S` | v1.2 | itecomdbgr no-output watchdog for full image flashing |
| `MAUI_H2H_FW_DIR` | v1.2 | Directory with the operator-provided H2H (PL27A1) images |

## Safety rules

* Every step log carries a `STATUS:` line. Missing or unknown status is a
  failure, and a bypassed step is reported as BYPASSED, never as passed.
* One console lock covers the functional cards, MCU/App/full-image/H2H
  flashing and PDC flashing. Operations on a console in use are refused.
* The station refuses to act when it cannot tell which board is the unit
  (several FTDI boards, ADB device that is not the unit, unresolved console).
* The approved MCU App builds come from the bundled `STABLE.json` files. On
  v1.2 the MCU version check fails when a `STABLE.json` is invalid.
* Firmware picked by hand on the v1.2 programming page is logged with its md5
  and marked when it is not the stable image.
