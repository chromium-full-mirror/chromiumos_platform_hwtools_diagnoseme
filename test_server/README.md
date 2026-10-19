# Custom Debian 13 (Trixie) Preseed Installer & Offline Ecosystem

This project provides a system for generating a custom Debian 13 (Trixie) installation ISO that automatically configures a workstation for ChromeOS Hardware Tools, including custom drivers, Docker, XFCE4 desktop, and offline execution/updating tools.

## Quick Start: Generate the USB Stick

1. **Build the ISO:**
   Run the provided build script for one edition (see `editions.json`). It will download the official Debian 13.3.0 netinst ISO, download the latest stable Maui firmware, embed the edition's Docker images and Debian package, and inject the `preseed.cfg`.
   ```bash
   ./build_iso.sh --edition maui-v1.2             # pull the released :maui-v1.2 images
   ./build_iso.sh --edition maui-v1.1 --build-local  # or build them from this checkout
   ```
   The result is `custom-debian-preseed-<edition>.iso` (`--output` overrides it).

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
  * Copies bundled `offline_assets/` (Docker images, Maui firmware, and Debian package) to `/var/lib/diagnoseme/`.
  * Configures LightDM autologin for user `diagnoseme` into an XFCE session.
  * Installs desktop shortcuts (**Start DiagnoseMe**, **Stop DiagnoseMe**, **Offline Update**, **Manufacturing Dashboard**, **Web UI**) onto `/home/diagnoseme/Desktop/`.

### 2. First-Boot Phase (Automated Setup)
After the first reboot, `diagnoseme-setup.service` performs one-time initialization:
* Loads embedded Docker images (`docker load -i /var/lib/diagnoseme/images/*.tar.gz`).
* Installs the `diagnoseme` Debian package.
* Triggers a **DKMS build** of the `r8152` Realtek driver against the active running kernel.
* Blacklists the conflicting `cdc_ether` module and updates `initramfs`.
* Automatically reboots the machine.

### 3. Second-Boot Phase (Interactive XFCE Desktop Ready)
* Boots directly into the **XFCE4 Desktop** with user `diagnoseme` logged in.
* Double-click **"Start DiagnoseMe (Offline)"** (or run `start-diagnoseme`) to bring up container services and automatically launch Google Chrome to `http://localhost:8888/maui-initial-flashing`.

---

## Managing DiagnoseMe & Offline Updates

### CLI Commands
* `start-diagnoseme [--offline | --online] [--no-browser]`: Starts DiagnoseMe testing containers and opens Google Chrome to Maui Station 1.
* `stop-diagnoseme`: Stops all DiagnoseMe containers.
* `diagnoseme-dashboard [--watch | --json | --all]`: Computes real-time Maui manufacturing statistics, pass/fail yields, and stage breakdowns.
* `diagnoseme-logs [service | --device <SERIAL>]`: Follows container logs in real time or finds serial results.
* `diagnoseme-logs --collect [SERIAL]`: Collects results, device and server logs, container logs, configuration, firmware versions, `lsusb` and `dmesg` into one `diagnoseme_logs_<station>_<SERIAL|all>_<date>_<time>.zip` in the current directory (send it with bug reports).
* `sudo diagnoseme-offline-update [bundle_path]`: Updates Docker images, `.deb`, and Maui firmware from a USB stick.

### Building an Offline Update Bundle for Factory USBs
From the repository root on a connected development machine:
```bash
./development_environment/build-offline-bundle.sh --edition maui-v1.2 \
    [--build-local | --use-local]
```
This exports the edition's Docker images (tagged with the station tag, e.g. `:maui` for `maui-v1.1`), copies/downloads the edition's firmware components, builds the edition's Debian package (Maui editions include the maui-utils image), computes SHA256 checksums, and creates a ready-to-copy update directory and ZIP file in `offline_update_bundle/<edition>/`.

### Maui Firmware and maui-utils Versions
Builds and stations always use the releases marked stable in the public `gs://maui-firmware` bucket: there are no version options or pinned versions.

* **Firmware:** `tools/maui_fw_fetch.py` downloads the edition's firmware components (`editions.json` `"firmware"`) into `firmware/<component>/` when the ISO or an offline bundle is built (`--skip-fw` skips the download; `dist/firmware` is still bundled) and when a station starts with `start-diagnoseme --online` (`FIRMWARE_COMPONENTS` in `/etc/diagnoseme/config.env`; a failed download only prints a warning). Each component follows the stable marker of the edition's Maui device (`editions.json` `"maui_device"`, `MAUI_DEVICE` in `config.env`), an empty file in the version directory, e.g. `pdc-fw/v20_0325_100W/stable.maui_v11.pdcfw`:

  | Component | Bucket directory | Stable marker | Payload |
  |---|---|---|---|
  | `mcu` | `mcu-fw/mspm0/<version>/` | `stable.<device>.mcufw` | `zephyr.txt` |
  | `pdc` | `pdc-fw/<version>/` | `stable.<device>.pdcfw` | `<version>.bin` |
  | `mcu_app` (Maui v1.2) | `mcu-fw/maui_v12/<version>/` | `stable.<device>.mcufw` | `maui_app_signed.bin` |
  | `mcu_full` (Maui v1.2) | `mcu-fw/maui_v12/<version>/` | `stable.<device>.mcufw` | `maui_full_flash_1mb.bin` (exactly 1,048,576 bytes) |

  A missing marker fails the fetch (for the Maui v1.2 components `mcu_app` and `mcu_full` it is only a warning). Downloads are size- and md5-checked and atomic. `firmware/<component>/STABLE.json` records the stable image as `{"version", "file", "md5", "source"}`; when the stable version changes, the payload named by the previous `STABLE.json` is deleted (nothing else is). `--dry-run` shows what would be fetched.
* **Flashing:** station 1 flashes the MCU and PDC images named by `STABLE.json` after checking their md5. Without a manifest, the only image of the component directory is flashed with a warning; several images are an error. An explicitly selected PDC version is flashed as chosen.
* **maui-utils:** Maui editions always bundle the stable maui-utils release, the `<version>` marked by `docker/<version>/stable.dockertag` in `gs://maui-firmware` (`tools/fetch_maui_utils.sh`); there is no version option. The deb records it in `config.env` as `MAUI_UTILS_IMAGE=maui-utils:<version>`; a server without `MAUI_UTILS_IMAGE` uses the newest local `maui-utils:*` image. Developer-only override: `--maui-utils-tar <file>` (`build_iso.sh`, `build-offline-bundle.sh`, `build-diagnoseme.sh`) bundles a local `docker save` tar of an unreleased build instead.
