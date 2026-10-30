# Custom Debian 13 (Trixie) Preseed Installer & Offline Ecosystem

This project provides a system for generating a custom Debian 13 (Trixie) installation ISO that automatically configures a workstation for ChromeOS Hardware Tools, including custom drivers, Docker, XFCE4 desktop, and offline execution/updating tools.

## Quick Start: Generate the USB Stick

1. **Build the ISO:**
   Run the provided build script for one edition (see `editions.json`). It will download the official Debian 13.3.0 netinst ISO, download the latest stable Maui firmware, embed the edition's Docker images and Debian package, and inject the `preseed.cfg`.
   ```bash
   ./build_iso.sh --edition maui-v1.2             # the release images of this commit (:maui-v1.2-<commit>)
   ./build_iso.sh --edition maui-v1.1 --build-local  # or build them from this checkout
   ```
   The result is `custom-debian-preseed-<edition>.iso` (`--output` overrides it).
   For Maui editions the build fails if no firmware of a component (e.g. MCU or PDC) could be staged (from GCS or `dist/firmware/<component>/`), because a Maui station ISO must contain the firmware. Pass `--skip-fw` only to build an ISO without downloading the firmware on purpose.

2. **Identify your USB Drive:**
   Plug in your USB stick and find its device name (e.g., `/dev/sda`, `/dev/sdb`).
   ```bash
   lsblk
   ```

3. **Flash the ISO:**
   Use `dd` to write the ISO to the **entire device** (replace `/dev/sda` with your actual device).
   ```bash
   sudo dd if=custom-debian-preseed-<edition>.iso of=/dev/sda bs=4M status=progress oflag=sync
   ```

---

## What the Preseed Does

The `preseed.cfg` automates the Debian installation process and handles several architectural challenges specific to modern hardware.

### 1. Installation Phase (Pre-Reboot)
* **Localization:** Sets locale to `en_US` and keyboard to `us`.
* **Network:** Sets the hostname to `diagnoseme-server`. The installer is set to `priority=high` to allow manual network setup if needed.
* **Partitioning:** Performs an `atomic` recipe (everything in one partition) on the primary disk.
* **Package Selection:**
  * Installs standard system tools, `ssh-server`, `sudo`, `dkms`, and `build-essential`.
  * Installs the **XFCE4 Desktop Environment** (`xfce4`, `xfce4-terminal`, `lightdm`).
* **Late Command:**
  * Configures GRUB with `nvme_core.default_ps_max_latency_us=0` for NVMe stability.
  * Copies bundled `offline_assets/` (Docker images, Maui firmware, and Debian package) to `/var/lib/diagnoseme/`, and the CLI tools, config and desktop launchers from the ISO.
  * Installs the edition's Debian package (output in `/var/log/diagnoseme/deb-install.log`). For the editions with the Realtek tools (`realtek_tools` in `editions.json`: servo-dolos) its postinst triggers a **DKMS build** of the `r8152` Realtek driver; for every edition it creates the log, device log and result directories below `/var/log/diagnoseme/` (owned by `diagnoseme`).
  * Adds user `diagnoseme` to the `docker` group and grants it passwordless `sudo`.
  * Configures LightDM autologin for user `diagnoseme` into an XFCE session.
  * Installs desktop shortcuts (**Start DiagnoseMe**, **Stop DiagnoseMe**, **Offline Update**, **Manufacturing Dashboard**, **Web UI**) onto `/home/diagnoseme/Desktop/`.
  * Loads `r8152` at boot, blacklists the conflicting `cdc_ether` module and updates `initramfs`.
  * Installs Google Chrome (if the download succeeds).
  * Online editions (servo-dolos, `MODE=online`): opens Chrome on `http://localhost:8888` at login (`~/.config/autostart`), as the pre-editions ISO did.
  * Installs `iso_setup/post-install-setup.sh` and creates `diagnoseme-setup.service` for the first boot.

The installer then reboots into the installed system.

### 2. First Boot (Automated Setup)
On the first boot, `diagnoseme-setup.service` runs `post-install-setup.sh`:
* Loads the embedded Docker images (`docker load -i /var/lib/diagnoseme/images/*.tar.gz`).
* Online editions (servo-dolos): adds the `diagnoseme-deb` apt source (`https://us-apt.pkg.dev/projects/chromeos-hw-tools`) and its key, as the pre-editions ISO did, and installs the package from it, so the station keeps getting package updates with `apt upgrade`. Maui editions stay offline.
* Checks that the edition's package is installed (`dpkg-query`); if not, it writes `/var/log/diagnoseme/INSTALL_FAILED` and `DIAGNOSEME_INSTALL_FAILED.txt` on the desktop.
* Disables itself and removes its setup script once every step succeeded (otherwise it runs again on the next boot). There is no extra reboot.

The machine boots directly into the **XFCE4 Desktop** with user `diagnoseme` logged in.
* Double-click **"Start DiagnoseMe (Offline)"** (or run `start-diagnoseme`) to bring up container services and automatically launch Google Chrome to the edition's start page (`UI_URL` in `/etc/diagnoseme/config.env`, e.g. `http://localhost:8888/maui/v1.1/provisioning`).

---

## Managing DiagnoseMe & Offline Updates

### CLI Commands
* `start-diagnoseme [--offline | --online] [--no-browser]`: Starts DiagnoseMe testing containers and (Maui editions) opens Google Chrome to the edition's start page.
* `stop-diagnoseme`: Stops all DiagnoseMe containers.
* `diagnoseme-dashboard [--watch | --json | --all]`: Computes real-time Maui manufacturing statistics, pass/fail yields, and stage breakdowns.
* `diagnoseme-logs [service]`: Follows container logs in real time (all containers by default).
* `diagnoseme-logs --device <SERIAL>`: Lists the result files and device logs of a device (a bare `<SERIAL>` that is not a service name works too).
* `diagnoseme-logs --collect [SERIAL]`: Collects results, device and server logs, container logs, configuration, firmware versions, `lsusb` and `dmesg` into one `diagnoseme_logs_<station>_<SERIAL|all>_<date>_<time>.zip` in the current directory (send it with bug reports).
* `sudo diagnoseme-offline-update [bundle_path]`: Updates Docker images, `.deb`, and Maui firmware from a USB stick. `bundle_path` may be the bundle directory or its `.zip`; without it, mounted media and then unmounted drives are searched.

### Log and Result Locations
* Device logs: `/var/log/diagnoseme/devices/<SERIAL>/`
* Final results: the edition's results directory (`DIAGNOSEME_RESULTS_DIR` in `config.env`), `/var/log/diagnoseme/maui_v11_results/` for Maui v1.1 and `/var/log/diagnoseme/maui_v12_results/` for Maui v1.2
* Container logs: `/var/log/diagnoseme/{rpcserver,nginx,envoy}/`
* Maui v1.2 H2H (PL27A1) firmware images: not part of DiagnoseMe; copy the `*.bin` file to `~/diagnoseme_h2h_firmware/` of the desktop user (`MAUI_H2H_FW_DIR` in `config.env`), which the server mounts read-only.

Results in `~/diagnoseme_results/` from older releases are still shown by `diagnoseme-dashboard` and `diagnoseme-logs`.

### Station Configuration
Every station runs one edition (`editions.json`); the ISO, the offline bundle and the Debian package are built per edition (`--edition` is required, there is no default edition). The shipped editions are `servo-dolos` (every Servo and Dolos page, package `diagnoseme`, images `:servo-dolos` and `:release`, its stations follow `:release`, ISO `custom-debian-preseed-latest.iso`) and `maui-v1.1` (package `diagnoseme-maui-v1.1`, ISO `custom-debian-preseed-maui-v1.1-latest.iso`). The edition packages conflict with / replace each other and `diagnoseme`. The package installs `/etc/diagnoseme/config.env` rendered for the edition by `tools/edition_info.py render-config`:
* Maui editions: the Maui station configuration (offline mode, the edition's images, browser auto-start, firmware components, maui-utils image, results directory, `COMPOSE_FILE` with the Maui compose override `docker-compose-maui.yaml`, which alone mounts the firmware, maui-utils staging, device log and results directories) and the Maui desktop launchers.
* servo-dolos (and the unshipped split editions): the generic online configuration (`MODE=online`, `DOCKER_TAG=<edition>`, `release` for servo-dolos, `PULL_POLICY=always`, watchtower, no browser start, no Maui keys) and only a `DiagnoseMe Web` menu entry.

Without a `config.env` (Servo / Dolos stations installed before the editions), `start-diagnoseme` pulls the `:release` images and runs watchtower; `apt upgrade` to the `servo-dolos` package keeps them online.

`config.env` belongs to the package and is replaced on every upgrade. Station specific settings (`STATION_ID`, `RESULTS_SERVER`, the `MAUI_*` test overrides, ...) go into `/etc/diagnoseme/config.local.env`: the package never ships or overwrites it, and `start-diagnoseme`, `stop-diagnoseme`, `diagnoseme-logs`, `diagnoseme-dashboard` and `maui-flash` read it after `config.env`. The first upgrade from a package without `config.local.env` (the pre-editions packages) moves the station specific values of the old `config.env` that differ from the new one into it; empty values, the old default station IDs (`MAUI_STATION_1`, `MAUI_V1_1_STATION`) and edition switches are skipped.

`start-diagnoseme --online` always pulls the latest images and runs watchtower; on Maui stations it also downloads the latest stable Maui firmware into `/var/lib/diagnoseme/firmware/<component>/` (skipped when the `STABLE.json` there already names the same version and MD5; failures are not fatal). In offline mode watchtower is not started.

### Releasing an Edition
`dockerfiles/cloudbuild_release.yaml` builds one edition: every release trigger sets `_EDITION` (the existing Servo / Dolos trigger needs `_EDITION=servo-dolos`; add a trigger per Maui edition if wanted) and `_COMMIT_SHA`. It pushes the images as `:<edition>-<commit>` and uploads the edition's deb and ISO. Online stations only get the new images after a promotion, and `tools/promote.sh` is the only way to promote:
```bash
tools/promote.sh --edition servo-dolos --commit <sha> [--dry-run]
```
It moves the edition tag and its `docker_tag_aliases` (servo-dolos: `:servo-dolos` and `:release`, which every Servo / Dolos station follows) for the ui, server and envoy images. Do not move `:release` by hand, and never promote a CQ image (CQ pushes the development `all` edition).

### Building an Offline Update Bundle for Factory USBs
From the repository root on a connected development machine:
```bash
./development_environment/build-offline-bundle.sh --edition maui-v1.2 \
    [--build-local | --use-local]
```
This exports the edition's Docker images of `--tag` (default `<edition>-<commit>`, the release build of the checked out commit; the server image must be the edition, there is no fallback to `:latest`), tagged with the station tags, e.g. `:maui-v1.1` ( the old `:maui` tag is not saved, the bundle's deb switches the station to `:maui-v1.1`), copies/downloads the edition's firmware components (failing if none could be staged for a component, or if a `firmware_stable_required` component, e.g. `mcu` and `pdc` of maui-v1.1, has no valid `STABLE.json`, unless `--allow-missing-fw` is given), builds the edition's Debian package (Maui editions include the maui-utils image), computes SHA256 checksums, and creates a ready-to-copy update directory and ZIP file in `offline_update_bundle/<edition>/`. On the station, run `sudo ./install-update.sh` from the bundle directory (or `sudo diagnoseme-offline-update`). `MANIFEST.json` names the bundle's edition, package, commit and the image ids and digests; a firmware component with a `STABLE.json` replaces the station's directory. The edition and package are checked: the update refuses a bundle of another edition than the station's (old Maui stations without `DIAGNOSEME_EDITION` count as `maui-v1.1`, the old `diagnoseme` package without `config.env` as `servo-dolos`) unless `--switch-edition` is given (`sudo ./install-update.sh --switch-edition`), and a deb whose `Package` is not the edition's.

`build_iso.sh` reuses this script to embed the same images, firmware and Debian package into the ISO.

### Maui Firmware and maui-utils Versions
Builds and stations always use the releases marked stable in the public `gs://maui-firmware` bucket: there are no version options or pinned versions.

* **Firmware:** `tools/maui_fw_fetch.py` (installed on stations as `fetch-maui-firmware`) downloads the edition's firmware components (`editions.json` `"firmware"`) into `firmware/<component>/` when the ISO or an offline bundle is built (`--skip-fw` skips the download; `dist/firmware` is still bundled) and when a Maui station starts with `start-diagnoseme --online` (`FIRMWARE_COMPONENTS` in `/etc/diagnoseme/config.env`; a failed download only prints a warning). Each component follows the stable marker of the edition's Maui device (`editions.json` `"maui_device"`, `MAUI_DEVICE` in `config.env`), an empty file in the version directory, e.g. `pdc-fw/v20_0325_100W/stable.maui_v11.pdcfw`:

  | Component | Bucket directory | Stable marker | Image |
  |---|---|---|---|
  | `mcu` | `mcu-fw/mspm0/<version>/` | `stable.<device>.mcufw` | the `*.txt` file (`zephyr.txt`) |
  | `pdc` | `pdc-fw/<version>/` | `stable.<device>.pdcfw` | the `*.bin` file (`<version>.bin`) |
  | `mcu_app` (Maui v1.2) | `mcu-fw/maui_v12/<version>/` | `stable.<device>.mcufw` | `maui_app_signed.bin` |
  | `mcu_full` (Maui v1.2) | `mcu-fw/maui_v12/<version>/` | `stable.<device>.mcufw` | `maui_full_flash_1mb.bin` (exactly 1,048,576 bytes) |

  A missing marker fails the fetch (for the Maui v1.2 components `mcu_app` and `mcu_full` it is only a warning). If a version directory holds several images, the preferred name (else the alphabetically first) is used with a warning. Downloads are size- and md5-checked. `firmware/<component>/` is replaced as a whole (downloaded next to it, then swapped in, so a failed download keeps the previous firmware) and holds exactly the stable image and `STABLE.json`, which records it as `{"version", "file", "md5", "source"}`. Nothing is downloaded when `STABLE.json` already names the same version and md5 (`--force` downloads anyway); `--dry-run` shows what would be fetched.
* **Flashing:** station 1 flashes the MCU and PDC images named by `STABLE.json` after checking their md5.
* **maui-utils:** Maui editions always bundle the stable maui-utils release, the `<version>` marked by `docker/<version>/stable.dockertag` in `gs://maui-firmware` (`tools/fetch_maui_utils.sh`, which downloads it with `tools/maui_fw_fetch.py maui-utils`: the same marker resolution with numeric version order, a crc32c check because the bucket has no md5 of this composite object, and a `STABLE.json` with its md5); there is no version option. A Maui deb build fails without it (`ALLOW_NO_MAUI_UTILS=1` for development builds only). `start-diagnoseme` checks the md5 of the bundled tar against its `STABLE.json` before `docker load`. The deb records it in `config.env` as `MAUI_UTILS_IMAGE=maui-utils:<version>`; a server without `MAUI_UTILS_IMAGE` uses the newest local `maui-utils:*` image. Developer-only override: `--maui-utils-tar <file>` (`build_iso.sh`, `build-offline-bundle.sh`, `build-diagnoseme.sh`) bundles a local `docker save` tar of an unreleased build instead.
