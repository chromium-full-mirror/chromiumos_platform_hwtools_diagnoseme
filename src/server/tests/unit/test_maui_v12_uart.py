# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Unit tests for the Maui v1.2 USB-UART bridge scan (maui_v12_uart)."""

import os
import pty
import tempfile
import termios
import unittest

from server import maui_v12_uart as uart


_PCI = "devices/pci0000:00/0000:00:14.0"


class FakeSysfs:
    """A fake /sys and /dev tree laid out like the kernel's."""

    def __init__(self, root: str):
        self.root = root
        self.sys = os.path.join(root, "sys")
        self.dev = os.path.join(root, "dev")
        os.makedirs(os.path.join(self.sys, "class", "tty"))
        os.makedirs(os.path.join(self.dev, "serial", "by-id"))

    def _write(self, path: str, value: str) -> None:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(value + "\n")

    def _driver_link(self, link: str, bus: str, driver: str) -> None:
        target = os.path.join(self.sys, "bus", bus, "drivers", driver)
        os.makedirs(target, exist_ok=True)
        os.symlink(target, link)

    def usb_device(self, usb_path: str, vid: str, pid: str, **strings) -> str:
        """Creates a USB device, e.g. usb_path '1-2' or '1-2.3'."""
        bus = usb_path.split("-")[0]
        parts = usb_path.split("-")[1].split(".")
        chain = [f"{bus}-{'.'.join(parts[:i + 1])}" for i in range(len(parts))]
        dev_dir = os.path.join(self.sys, _PCI, f"usb{bus}", *chain)
        self._write(os.path.join(dev_dir, "idVendor"), vid)
        self._write(os.path.join(dev_dir, "idProduct"), pid)
        self._write(os.path.join(dev_dir, "devnum"), "5")
        for name in ("manufacturer", "product", "serial"):
            if strings.get(name) is not None:
                self._write(os.path.join(dev_dir, name), strings[name])
        return dev_dir

    def interface(self, dev_dir: str, number: int, driver: str) -> str:
        """Creates interface <dev>:1.<number> bound to the USB |driver|."""
        iface = os.path.join(dev_dir, f"{os.path.basename(dev_dir)}:1.{number}")
        self._write(os.path.join(iface, "bInterfaceNumber"), f"{number:02x}")
        self._driver_link(os.path.join(iface, "driver"), "usb", driver)
        return iface

    def _tty(self, parent: str, name: str) -> None:
        tty_dir = os.path.join(parent, "tty", name)
        os.makedirs(tty_dir)
        os.symlink(parent, os.path.join(tty_dir, "device"))
        os.symlink(tty_dir, os.path.join(self.sys, "class", "tty", name))
        self._write(os.path.join(self.dev, name), "")

    def acm(self, iface: str, name: str) -> None:
        """cdc_acm: the tty device is the USB interface itself."""
        self._tty(iface, name)

    def usb_serial(self, iface: str, name: str, port_driver: str, latency=None):
        """usb-serial: the tty device is a port below the interface."""
        port = os.path.join(iface, name)
        os.makedirs(port)
        self._driver_link(os.path.join(port, "driver"), "usb-serial", port_driver)
        if latency is not None:
            self._write(os.path.join(port, "latency_timer"), latency)
        self._tty(port, name)
        return port

    def by_id(self, link_name: str, tty: str) -> str:
        """Creates /dev/serial/by-id/<link_name> -> ../../<tty> like udev."""
        link = os.path.join(self.dev, "serial", "by-id", link_name)
        os.symlink(os.path.join("..", "..", tty), link)
        return link


class TestScanUartBridges(unittest.TestCase):
    """scan_uart_bridges() on a fake sysfs tree."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()  # pylint: disable=consider-using-with
        self.addCleanup(tmp.cleanup)
        self.fs = FakeSysfs(tmp.name)

    def _scan(self):
        return uart.scan_uart_bridges(self.fs.sys, self.fs.dev)

    def _add_excluded_devices(self):
        fs = self.fs
        # Maui v1.2 MCU console (cdc_acm, Google VID).
        mcu = fs.usb_device(
            "1-3.1", "18d1", "5085", product="Maui Diagnostic Controller"
        )
        fs.acm(fs.interface(mcu, 0, "cdc_acm"), "ttyACM0")
        # Ti50 CCD console of the DUT (usb_serial_simple "google").
        ccd = fs.usb_device("1-4", "18d1", "504a", product="Ti50")
        fs.usb_serial(fs.interface(ccd, 1, "usb_serial_simple"), "ttyUSB5", "google")
        # Google device with a cdc_acm interface (e.g. servo, H2H tooling).
        servo = fs.usb_device("1-8", "18d1", "520d", product="Servo")
        fs.acm(fs.interface(servo, 2, "cdc_acm"), "ttyACM3")
        # Maui v1.1 on-board FT232R after Station 1 (0403:6002).
        v11 = fs.usb_device("1-5", "0403", "6002", product="Maui v1.1")
        fs.usb_serial(fs.interface(v11, 0, "ftdi_sio"), "ttyUSB4", "ftdi_sio", "16")
        # A modem (option driver) is no UART bridge.
        modem = fs.usb_device("1-9", "1199", "9071", product="Modem")
        fs.usb_serial(fs.interface(modem, 3, "option"), "ttyUSB6", "option1")

    def _add_bridges(self):
        fs = self.fs
        ch340 = fs.usb_device("1-6", "1a86", "7523", product="USB Serial")
        fs.usb_serial(fs.interface(ch340, 0, "ch341"), "ttyUSB1", "ch341-uart")
        pico = fs.usb_device(
            "1-2",
            "2e8a",
            "000c",
            manufacturer="Raspberry Pi",
            product="Debugprobe on Pico (CMSIS-DAP)",
            serial="E6614103E7",
        )
        fs.acm(fs.interface(pico, 1, "cdc_acm"), "ttyACM1")
        pl2303 = fs.usb_device("1-11", "067b", "23a3", product="USB-Serial Controller")
        fs.usb_serial(fs.interface(pl2303, 0, "pl2303"), "ttyUSB2", "pl2303")
        cp2102 = fs.usb_device(
            "1-10", "10c4", "ea60", manufacturer="Silicon Labs", serial="0001"
        )
        fs.usb_serial(fs.interface(cp2102, 0, "cp210x"), "ttyUSB3", "cp210x")
        ftdi = fs.usb_device(
            "1-7",
            "0403",
            "6001",
            manufacturer="FTDI",
            product="TTL232R-3V3",
            serial="FT9XYZ",
        )
        fs.usb_serial(fs.interface(ftdi, 0, "ftdi_sio"), "ttyUSB0", "ftdi_sio", "16")
        fs.by_id("usb-FTDI_TTL232R-3V3_FT9XYZ-if00-port0", "ttyUSB0")

    def test_keeps_bridges_in_driver_order(self):
        """Bridges come in driver order, other ttys are dropped."""
        self._add_excluded_devices()
        self._add_bridges()
        bridges = self._scan()
        self.assertEqual(
            [os.path.basename(b.dev_node) for b in bridges],
            ["ttyUSB0", "ttyUSB3", "ttyACM1", "ttyUSB2", "ttyUSB1"],
        )
        self.assertEqual(
            [b.driver for b in bridges],
            ["ftdi_sio", "cp210x", "cdc_acm", "pl2303", "ch341"],
        )

    def test_excludes_google_and_maui_v11_devices(self):
        """Google devices, the v1.1 FT232R and modems are never offered."""
        self._add_excluded_devices()
        self.assertEqual(self._scan(), [])

    def test_reports_bridge_details(self):
        """Identity, by-id link, USB path and labels are reported."""
        self._add_bridges()
        ftdi = self._scan()[0]
        self.assertEqual(ftdi.dev_node, os.path.join(self.fs.dev, "ttyUSB0"))
        self.assertEqual(
            os.path.basename(ftdi.by_id), "usb-FTDI_TTL232R-3V3_FT9XYZ-if00-port0"
        )
        self.assertEqual((ftdi.vid, ftdi.pid), (0x0403, 0x6001))
        self.assertEqual(
            (ftdi.manufacturer, ftdi.product, ftdi.serial),
            ("FTDI", "TTL232R-3V3", "FT9XYZ"),
        )
        self.assertEqual((ftdi.usb_path, ftdi.interface), ("1-7", 0))
        self.assertTrue(ftdi.port_sys_path.endswith("1-7:1.0/ttyUSB0"))
        self.assertEqual(ftdi.name, "usb-FTDI_TTL232R-3V3_FT9XYZ-if00-port0")
        self.assertIn("0403:6001, ftdi_sio, USB 1-7", ftdi.label)
        pico = next(b for b in self._scan() if b.driver == "cdc_acm")
        self.assertEqual(pico.by_id, "")
        self.assertEqual(pico.interface, 1)
        self.assertEqual(
            pico.name, "Raspberry Pi Debugprobe on Pico (CMSIS-DAP) [E6614103E7]"
        )

    def test_same_driver_sorted_by_usb_path_and_interface(self):
        """Same driver: natural USB path order, then interface."""
        fs = self.fs
        for usb_path, ttys in (
            ("1-10", ("ttyUSB0",)),
            ("1-2.4", ("ttyUSB2", "ttyUSB1")),
        ):
            dev = fs.usb_device(usb_path, "0403", "6010", product="FT2232H")
            for number, tty in enumerate(ttys):
                fs.usb_serial(fs.interface(dev, number, "ftdi_sio"), tty, "ftdi_sio")
        bridges = self._scan()
        self.assertEqual(
            [(b.usb_path, b.interface) for b in bridges],
            [("1-2.4", 0), ("1-2.4", 1), ("1-10", 0)],
        )

    def test_ignores_ttys_without_usb_interface(self):
        """ttys without a USB interface are skipped."""
        os.makedirs(os.path.join(self.fs.sys, "class", "tty", "ttyUSB9"))
        platform = os.path.join(self.fs.sys, "devices", "platform", "serial8250")
        os.makedirs(os.path.join(platform, "tty", "ttyACM9"))
        os.symlink(platform, os.path.join(platform, "tty", "ttyACM9", "device"))
        os.symlink(
            os.path.join(platform, "tty", "ttyACM9"),
            os.path.join(self.fs.sys, "class", "tty", "ttyACM9"),
        )
        self.assertEqual(self._scan(), [])

    def test_find_and_default_bridge(self):
        """Bridges are found by dev node or by-id; the first is the default."""
        self._add_bridges()
        bridges = self._scan()
        self.assertIs(uart.default_bridge(bridges), bridges[0])
        self.assertIsNone(uart.default_bridge([]))
        ftdi = bridges[0]
        self.assertIs(uart.find_bridge(ftdi.by_id, bridges), ftdi)
        self.assertIs(uart.find_bridge(ftdi.dev_node, bridges), ftdi)
        self.assertIsNone(uart.find_bridge("/dev/ttyACM0", bridges))
        self.assertIsNone(uart.find_bridge("", bridges))


class TestPrepareBridge(unittest.TestCase):
    """FTDI latency timer and termios normalization."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()  # pylint: disable=consider-using-with
        self.addCleanup(tmp.cleanup)
        self.fs = FakeSysfs(tmp.name)

    def _bridge(self, driver="ftdi_sio", latency="16", port_driver="ftdi_sio"):
        dev = self.fs.usb_device("1-7", "0403", "6001", product="TTL232R-3V3")
        self.fs.usb_serial(
            self.fs.interface(dev, 0, driver), "ttyUSB0", port_driver, latency
        )
        return uart.scan_uart_bridges(self.fs.sys, self.fs.dev)[0]

    def _latency(self, bridge):
        with open(
            os.path.join(bridge.port_sys_path, "latency_timer"), encoding="utf-8"
        ) as f:
            return f.read().strip()

    def test_sets_ftdi_latency_timer(self):
        """The FTDI latency timer is set to 1 ms once."""
        bridge = self._bridge()
        self.assertIn("set to 1 ms (was 16)", uart.set_ftdi_latency_timer(bridge))
        self.assertEqual(self._latency(bridge), "1")
        self.assertIn("already 1 ms", uart.set_ftdi_latency_timer(bridge))

    def test_latency_timer_failure_is_reported(self):
        """A failed write is reported, not raised."""
        bridge = self._bridge(latency=None)
        os.makedirs(os.path.join(bridge.port_sys_path, "latency_timer"))
        self.assertIn("Could not set", uart.set_ftdi_latency_timer(bridge))

    def test_latency_timer_only_for_ftdi(self):
        """Other drivers have no latency timer."""
        bridge = self._bridge(driver="cp210x", latency=None, port_driver="cp210x")
        self.assertEqual(uart.set_ftdi_latency_timer(bridge), "")

    def test_normalize_termios_clears_odd_parity(self):
        """Odd parity left by a previous user is cleared."""
        pty_fd, tty_fd = pty.openpty()
        self.addCleanup(os.close, pty_fd)
        self.addCleanup(os.close, tty_fd)
        name = os.ttyname(tty_fd)
        attrs = termios.tcgetattr(tty_fd)
        attrs[2] |= termios.PARENB | termios.PARODD
        termios.tcsetattr(tty_fd, termios.TCSANOW, attrs)
        self.assertIn("Cleared", uart.normalize_termios(name))
        cflag = termios.tcgetattr(tty_fd)[2]
        self.assertFalse(cflag & termios.PARODD)
        self.assertEqual(uart.normalize_termios(name), "")

    def test_normalize_termios_reports_open_errors(self):
        """A port that cannot be opened is reported."""
        self.assertIn(
            "Could not open", uart.normalize_termios(os.path.join(self.fs.dev, "nope"))
        )

    def test_prepare_bridge_is_best_effort(self):
        """Each preparation step reports instead of raising."""
        bridge = self._bridge()
        lines = uart.prepare_bridge(bridge)
        # The fake dev node is a plain file: the termios step reports it.
        self.assertEqual(len(lines), 2)
        self.assertIn("latency_timer", lines[0])
        self.assertIn("termios", lines[1])


if __name__ == "__main__":
    unittest.main()
