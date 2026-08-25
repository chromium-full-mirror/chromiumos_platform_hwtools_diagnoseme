# Custom Debian 13 (Trixie) Preseed Installer & Offline Ecosystem

This project provides a system for generating a custom Debian 13 (Trixie) installation ISO that automatically configures a workstation for ChromeOS Hardware Tools, including custom drivers, Docker, XFCE4 desktop, and offline execution/updating tools.

## Quick Start: Generate the USB Stick

1. **Build the ISO:**
   Run the provided build script. It will download the official Debian 13.3.0 netinst ISO, download the latest stable Maui firmware, embed initial Docker assets, and inject the `preseed.cfg`.
   ```bash
   ./build_iso.sh
   ```

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
* `diagnoseme-logs [service | <SERIAL>]`: Follows container logs in real time or finds serial results.
* `sudo diagnoseme-offline-update [bundle_path]`: Updates Docker images, `.deb`, and Maui firmware from a USB stick.

### Building an Offline Update Bundle for Factory USBs
From the repository root on a connected development machine:
```bash
./development_environment/build-offline-bundle.sh --tag maui
```
This downloads the latest stable MCU/PDC firmware from GCS, exports `:maui` Docker images, builds the Debian package, computes SHA256 checksums, and creates a ready-to-copy update directory and ZIP file.
