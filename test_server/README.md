# Custom Debian 13 (Trixie) Preseed Installer & Offline Ecosystem

This project provides a system for generating a custom Debian 13 (Trixie) installation ISO that automatically configures a workstation for ChromeOS Hardware Tools, including custom drivers, Docker, XFCE4 desktop, and offline execution/updating tools.

## Quick Start: Generate the USB Stick

1. **Build the ISO:**
   Run the provided build script. It will download the official Debian 13.3.0 netinst ISO, download the latest stable Maui firmware, embed initial Docker assets, and inject the `preseed.cfg`.
   ```bash
   ./build_iso.sh
   ```
   The build fails if no MCU or PDC firmware could be staged (from GCS or `dist/firmware/<mcu|pdc>/`), because a Maui station ISO must contain the firmware. Pass `--skip-fw` only to build an ISO without firmware on purpose.

2. **Identify your USB Drive:**
   Plug in your USB stick and find its device name (e.g., `/dev/sda`, `/dev/sdb`).
   ```bash
   lsblk
   ```

3. **Flash the ISO:**
   Use `dd` to write the ISO to the **entire device** (replace `/dev/sda` with your actual device).
   ```bash
   sudo dd if=custom-debian-preseed.iso of=/dev/sda bs=4M status=progress oflag=sync
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
  * Installs the `diagnoseme` Debian package. Its postinst triggers a **DKMS build** of the `r8152` Realtek driver and creates the log, device log and result directories below `/var/log/diagnoseme/` (owned by `diagnoseme`).
  * Adds user `diagnoseme` to the `docker` group and grants it passwordless `sudo`.
  * Configures LightDM autologin for user `diagnoseme` into an XFCE session.
  * Installs desktop shortcuts (**Start DiagnoseMe**, **Stop DiagnoseMe**, **Offline Update**, **Manufacturing Dashboard**, **Web UI**) onto `/home/diagnoseme/Desktop/`.
  * Loads `r8152` at boot, blacklists the conflicting `cdc_ether` module and updates `initramfs`.
  * Installs Google Chrome (if the download succeeds).
  * Creates the one-shot `diagnoseme-setup.service` for the first boot.

The installer then reboots into the installed system.

### 2. First Boot (Automated Setup)
On the first boot, `diagnoseme-setup.service` runs once:
* Loads the embedded Docker images (`docker load -i /var/lib/diagnoseme/images/*.tar.gz`).
* Disables itself and removes its setup script. There is no extra reboot.

The machine boots directly into the **XFCE4 Desktop** with user `diagnoseme` logged in.
* Double-click **"Start DiagnoseMe (Offline)"** (or run `start-diagnoseme`) to bring up container services and automatically launch Google Chrome to `http://localhost:8888/maui-initial-flashing`.

---

## Managing DiagnoseMe & Offline Updates

### CLI Commands
* `start-diagnoseme [--offline | --online] [--no-browser]`: Starts DiagnoseMe testing containers and opens Google Chrome to Maui Station 1.
* `stop-diagnoseme`: Stops all DiagnoseMe containers.
* `diagnoseme-dashboard [--watch | --json | --all]`: Computes real-time Maui manufacturing statistics, pass/fail yields, and stage breakdowns.
* `diagnoseme-logs [service]`: Follows container logs in real time (all containers by default).
* `diagnoseme-logs --device <SERIAL>`: Lists the result files and device logs of a device (a bare `<SERIAL>` that is not a service name works too).
* `sudo diagnoseme-offline-update [bundle_path]`: Updates Docker images, `.deb`, and Maui firmware from a USB stick. `bundle_path` may be the bundle directory or its `.zip`; without it, mounted media and then unmounted drives are searched.

### Log and Result Locations
* Device logs: `/var/log/diagnoseme/devices/<SERIAL>/`
* Final results: `/var/log/diagnoseme/maui_v11_results/`
* Container logs: `/var/log/diagnoseme/{rpcserver,nginx,envoy}/`

Results in `~/diagnoseme_results/` from older releases are still shown by `diagnoseme-dashboard` and `diagnoseme-logs`.

### Station Configuration
The ISO and offline bundle install the Maui station configuration `/etc/diagnoseme/config.env` (offline mode, `:maui` images, browser auto-start) and the Maui desktop launchers. The generic `diagnoseme` package (Servo/Dolos) ships no `config.env` and only a `DiagnoseMe Web` menu entry; `start-diagnoseme` then pulls the `:release` images and runs watchtower.

`start-diagnoseme --online` always pulls the latest images and runs watchtower; on Maui stations it also downloads the latest stable Maui firmware into `/var/lib/diagnoseme/firmware/<mcu|pdc>/` (skipped when the `STABLE.json` there already names the same version and MD5; failures are not fatal). In offline mode watchtower is not started.

### Building an Offline Update Bundle for Factory USBs
From the repository root on a connected development machine:
```bash
./development_environment/build-offline-bundle.sh --tag maui
```
This downloads the latest stable MCU/PDC firmware from GCS (failing if none could be staged, unless `--skip-fw` or `--allow-missing-fw` is given), exports `:maui` Docker images, builds the Debian package, computes SHA256 checksums, and creates a ready-to-copy update directory and ZIP file. On the station, run `sudo ./install-update.sh` from the bundle directory (or `sudo diagnoseme-offline-update`).

`build_iso.sh` reuses this script to embed the same images, firmware and Debian package into the ISO.
