# Servo V4.1 Manufacturing Test Specification

This document details the three-tier verification strategy used during the manufacturing and provisioning process for the Servo V4.1.

---

## Phase 1: Console Testing (Direct Hardware Verification)
**Requirement**: Host USB cable connected to the Servo. **No servod instance required.**

These tests interact directly with the Servo's MCU console via the serial port to verify identity and basic power state.

| Test Name | Command | Success Criteria |
| :--- | :--- | :--- |
| **lsusb Presence** | `lsusb` | Device `18d1:520d` (Google Servo V4.1) must be found on the host system. |
| **Verify Serial Number** | `serialno` | MCU console output must match the scanned serial number. |
| **Verify MAC Address** | `macaddr` | MCU console output must match the scanned Ethernet MAC address. |
| **PD Role Check** | `pd 0 state` | MCU must report a valid Power Delivery role (either `SRC` or `SNK`). |
| **I2C Scan** | `i2cscan 1` | MCU must detect all 7 critical onboard devices (`0x21`, `0x40`, `0x41`, `0x42`, `0x48`, `0x49`, `0x50`). |

---

## Phase 2: Functional Testing (USB Mux Verification)
**Requirement**: Servod running in **recovery mode** (`--recovery`). Two test USB sticks must be plugged into the Servo (Top and Bottom USB-A ports).

These tests verify the physical USB muxing logic using serial-number tracking to ensure the device actually switches paths on the system.

### USB Mux Verification Logic (Repeated for Top, Bottom, and uServo ports):
1.  **Host Connection**: Use `dut-control` to set the mux to host-visible state (`servo_sees_usbkey` for USB-A, `uservo` for uServo).
2.  **Discovery**: Locate the USB stick on the Genesys Host Hub (`05e3:0610` or `05e3:0625`) and capture its unique serial number from sysfs (`/sys/bus/usb/devices/.../serial`).
3.  **Mux Switch**: Use `dut-control` to set the mux to DUT-visible state (`dut_sees_usbkey` for USB-A, `fastboot` for uServo).
4.  **Path Verification**:
    *   Confirm the device has **disappeared** from the original Host Hub sysfs path.
    *   Confirm the device has **appeared** at a **different** physical path on the system.
    *   Confirm the **same serial number** is found at the new path.
    *   Confirm the parent hub at the new path is the Cypress DUT Hub (`04b4:6502` or `04b4:6500`).
5.  **Restore**: Switch the mux back to host-visible state and confirm the device returns to the original Host Hub path.

| Port Under Test | Control Name | Hub Port |
| :--- | :--- | :--- |
| **Top USB-A Port** | `top_usbkey_mux` | 2 |
| **Bottom USB-A Port** | `bottom_usbkey_mux` | 1 |
| **uServo USB-A Port** | `uservo_fastboot_mux_sel` | 4 |

---

## Phase 3: Integration Testing (System Connectivity)
**Requirement**: Servod running in **normal mode**. Servo connected to a **DUT** (ChromeOS Device) via the captive (white) cable.

These tests verify that the Servo can successfully manage a target device and monitor its power rails.

### 1. Console Connectivity
Checks if the Servo can correctly expose and communicate with the DUT's various internal UARTs.

| UART Interface | Servod Control | Verification |
| :--- | :--- | :--- |
| **EC Console** | `ec_uart_pty` | Verify the character device path exists on the host. |
| **AP Console** | `cpu_uart_pty` | Verify the character device path exists on the host. |
| **GSC Console** | `cr50_uart_pty` | Verify the character device path exists on the host. |

### 2. INA Sensor Verification (Voltage Rails)
Reads the onboard INA231 sensors via the Servo's I2C bus to ensure critical power rails are active and reporting non-zero voltage.

| Rail Name | Address | Description | Success Criteria |
| :--- | :--- | :--- | :--- |
| **ppdut5** | `0x80` | INA231 [U7] | Voltage > 0 mV |
| **ppchg5** | `0x82` | INA231 [U23] | Voltage > 0 mV |
| **ppservo5** | `0x84` | INA231 [U51] | Voltage > 0 mV |
