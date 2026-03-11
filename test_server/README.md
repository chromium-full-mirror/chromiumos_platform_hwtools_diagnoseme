# Custom Debian 13 (Trixie) Preseed Installer

This project provides a system for generating a custom Debian 13 (Trixie) installation ISO that automatically configures a workstation for ChromeOS Hardware Tools, including custom drivers, Docker, and a graphical environment.

## Quick Start: Generate the USB Stick

1. **Build the ISO:**
   Run the provided build script. It will download the official Debian 13.3.0 netinst ISO and inject the `preseed.cfg`.
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

The `preseed.cfg` automates the Debian installation process and handles several architectural challenges specific to modern hardware and the Debian Testing/Stable transition.

### 1. Installation Phase (Pre-Reboot)
*   **Localization:** Sets locale to `en_US` and keyboard to `us`.
*   **Network:** Sets the hostname to `diagnoseme-server`. The installer is set to `priority=high`, which forces it to **prompt the user for the network interface** (allowing manual Wi-Fi selection and WPA key entry).
*   **Partitioning:** Performs an `atomic` recipe (everything in one partition) on the primary disk.
*   **Package Selection:**
    *   Installs standard system tools and `ssh-server`.
    *   Installs a minimal GUI: `lightdm`, `openbox`, and `xserver-xorg`.
    *   **Note:** `install-recommends` is disabled to bypass a known bug in Debian Testing's `plymouth` package that causes installer crashes.
*   **Late Command:**
    *   Configures GRUB with `nvme_core.default_ps_max_latency_us=0` for NVMe stability.
    *   Creates a `post-install-setup.sh` script and a systemd "one-shot" service to run it on the first boot.

### 2. First-Boot Phase (Automated Setup)
After the first reboot, the `diagnoseme-setup.service` triggers the following logic:
*   **Driver Preparation:** Installs `build-essential`, `dkms`, and `linux-headers`.
*   **Repository Setup:** Adds the ChromeOS Artifact Registry for the `diagnoseme` package.
*   **Software Installation:**
    *   Installs `docker.io`, `docker-compose`, and `docker-cli` from official Debian repos.
    *   Installs the `diagnoseme` package.
*   **Driver Compilation:** The installation of `diagnoseme` triggers a **DKMS build** of the `r8152` Realtek driver. This is done on first-boot to ensure the driver is compiled against the active running kernel, avoiding header mismatches.
*   **Conflict Resolution:**
    *   Blacklists the conflicting `cdc_ether` module.
    *   Updates `initramfs` to include the new driver.
*   **Auto-Finalization:** The script disables its own systemd service and **automatically reboots the machine** a second time.

### 3. Second-Boot Phase (Ready to Use)
*   The machine boots with the `r8152` driver correctly loaded.
*   The system logs in automatically to the `diagnoseme` user.
*   Google Chrome launches in full-screen mode pointing to the local app.
*   The user is pre-added to the `docker` and `sudo` groups for immediate work.

## Troubleshooting
*   **Missing Docker command:** If `docker` is not found, verify the setup script finished; it requires a working internet connection during the first boot.
*   **GUI not starting:** Check `journalctl -u lightdm`. We explicitly install `xserver-xorg` to compensate for the disabled recommends.
