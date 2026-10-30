# Editions and releases

A DiagnoseMe build contains one product flavour, called an *edition*.
`editions.json` is the single registry. The build scripts read it through
`tools/edition_info.py`, the server reads it through `src/server/editions.py`,
and `src/server/tests/unit/test_editions.py` checks that the registry, the
Angular edition files, `angular.json` and the `Dockerfile.base` targets agree.

| Edition | Shipped | Package | Station image tag | ISO |
|---------|---------|---------|-------------------|-----|
| servo-dolos | yes | `diagnoseme` | `:release` | `custom-debian-preseed-latest.iso` |
| maui-v1.1 | yes | `diagnoseme-maui-v1.1` | `:maui-v1.1` | `custom-debian-preseed-maui-v1.1-latest.iso` |
| maui-v1.2 | yes | `diagnoseme-maui-v1.2` | `:maui-v1.2` | `custom-debian-preseed-maui-v1.2-latest.iso` |
| dolos, servo-v4p1, servo-micro | no | | | |
| all | no (development) | | | |

All edition packages conflict with and replace each other, so a station runs
exactly one edition.

## Building

* Local: `development_environment/build-diagnoseme.sh --edition <id>`. Without
  `--edition` it builds the development edition `all`.
* Release: `dockerfiles/cloudbuild_release.yaml`. The release build needs the
  `_EDITION` and `_COMMIT_SHA` substitutions and fails without them. Each
  edition needs its own trigger. The Servo/Dolos trigger sets
  `_EDITION=servo-dolos`.
* CQ builds every shipped edition (server base, deb, `tools/check_deb.py`) and
  runs the unit tests.
* Local builds name their intermediate images per edition
  (`server-base:build-<edition>`, `diagnoseme-deb-builder-<edition>`, ...), so
  builds of different editions can run in parallel.
* Every deb is versioned `1.1.<YYYYMMDD>` (UTC) by `tools/deb_version.sh`,
  above the `1.0.<unix seconds>` of the older debs. Two builds on the same day
  get the same version. `MANIFEST.json` records it.

Release images are tagged `<edition>-<commit>`.

### Realtek tools

Only the editions with `"realtek_tools": true` (servo-dolos, servo-v4p1, all:
the Servo v4p1 tooling) need `dist/rtunicpg.tar.bz2` and
`dist/r8152-2.21.4.tar.bz2`. `tools/fetch_rtk_tools.sh <edition>` fetches them
for every build and fails if it cannot. Only those debs ship `rtunicpg`, the
RTL8153 EEPROM files and the r8152 DKMS driver, and only they depend on
`dkms`, `build-essential` and the kernel headers. Maui debs and images build
without the files. On a station that moves from the old `diagnoseme` package
to a Maui package, postinst removes the stale r8152 DKMS registration. Every
deb still blacklists `cdc_ether`, and so does every ISO's preseed, which also
loads `r8152` at boot.

## Promoting

`tools/promote.sh --edition <id> --commit <sha> [--dry-run]` is the only way to
promote a build. It moves `:<edition>` and the edition's aliases. For
servo-dolos it also moves `:release`, which the Servo/Dolos stations follow.

## Station configuration

* `/etc/diagnoseme/config.env` belongs to the package. It is rendered per
  edition (`tools/edition_info.py render-config`) and replaced on every
  upgrade.
* Station-specific settings (`STATION_ID`, `RESULTS_SERVER`, `MAUI_*`
  overrides) go into `/etc/diagnoseme/config.local.env`. The package never
  ships or overwrites that file, and every station script reads it after
  `config.env`. The first upgrade from an older package moves real overrides
  there. It skips edition switches, empty values and old default station IDs.
* `docker-compose-prod.yaml` holds the generic services. Maui editions add
  `docker-compose-maui.yaml`, and Maui v1.2 also adds
  `docker-compose-maui-h2h.yaml`, through `COMPOSE_FILE` in `config.env`.
  Servo/Dolos stations get no Maui folders or mounts.
* For development with Maui, add `docker-compose-dev-maui.yaml` to the
  development `docker-compose.yaml`.

## Maui firmware and tools

* `tools/maui_fw_fetch.py` is the only firmware fetcher. It follows the
  per-device stable markers in `gs://maui-firmware`
  (`stable.<maui_device>.<suffix>`) and writes `<component>/STABLE.json`
  (`version`, `file`, `md5`, `source`). It checks the md5 and replaces a
  component directory atomically.
* Flashing fails closed. It fails when `STABLE.json` is present but invalid,
  and when there are several images without a `STABLE.json`. With no marker,
  exactly one image is used, with a warning.
* `firmware_stable_required` in `editions.json` lists the components that the
  ISO and the offline bundle cannot be built without.
* Maui host tools (`maui-flash`, `maui-ctl`, `fw-updater`) run in the
  maui-utils container through the host docker socket. Maui debs bundle the
  stable maui-utils release (`tools/fetch_maui_utils.sh`, md5 checked), and a
  Maui deb build without it fails unless `ALLOW_NO_MAUI_UTILS=1`.
* Flashing is simulated only when `DIAGNOSEME_SIMULATE_MAUI=1` is set.

## Offline bundles and ISOs

* `development_environment/build-offline-bundle.sh --edition <id>` exports
  `<edition>-<commit>` images (no `:latest` fallback). `MANIFEST.json` records
  the edition, the package, the commit and the image digests.
* `diagnoseme-offline-update` refuses a bundle of another edition unless
  `--switch-edition` is given. It checks the deb's `Package` field and
  replaces a firmware component directory that comes with a `STABLE.json`.
* `test_server/build_iso.sh --edition <id>` builds the per-edition ISO.
  * Maui ISOs install offline.
  * The servo-dolos ISO adds the `diagnoseme-deb` apt source and opens Chrome
    at login, as before editions existed.
  * If the deb install fails, the ISO leaves
    `/var/log/diagnoseme/INSTALL_FAILED` and writes a log to
    `/var/log/diagnoseme/deb-install.log`.

## Logs and results

* Device logs are in `/var/log/diagnoseme/devices/<SERIAL>/`.
* Results are in the edition's `results_dir`
  (`/var/log/diagnoseme/maui_v11_results` or
  `/var/log/diagnoseme/maui_v12_results`).
* `diagnoseme-logs` and `diagnoseme-dashboard` also read the legacy
  `~/diagnoseme_results`. `diagnoseme-logs --collect [SERIAL]` writes one zip
  with everything needed for debugging.
