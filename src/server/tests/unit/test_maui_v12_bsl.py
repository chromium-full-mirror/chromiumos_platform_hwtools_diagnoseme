# Copyright 2026 The ChromiumOS Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Tests for the guided BSL check and the MCU console clean-up of Maui v1.2."""

# pylint: disable=protected-access,missing-function-docstring,no-member
# pylint: disable=too-few-public-methods,attribute-defined-outside-init

import contextlib
import unittest
from unittest import mock

from server import maui_v12_bsl as bsl
from server import maui_v12_device as device
from server import maui_v12_profile as profile
from server import maui_v12_services
from server.generated import maui_manufacturing_pb2


SN = "MAUIV12H26090104"
GPIO = profile.BSL_GPIO_NAME

# Real console output of the partner test (b/574407540, bsl_button_test.log
# and the card 2 log of MAUIV12H26090104).
PARTNER_BSL_REPLY = (
    "--- 16 messages dropped ---\r\n"
    "[00:00:00.003,265] <inf> gpio: Initialized GPIOs\r\n"
    "maui$ gpio get ec_bsl_invoke\r\n"
    "[            ec_bsl_invoke] I = 1\r\n"
    "maui$ "
)
PARTNER_BOOT_LOG = (
    "--- 14 messages dropped ---\r\n"
    "[00:00:00.003,295] <inf> gpio: Initialized GPIOs\r\n"
    "[00:00:00.506,256] <inf> tps6699x: TPS6699X DUT (Port 1) Interrupt Unmasked\r\n"
    "[00:00:00.507,507] <inf> tps6699x: TPS6699X Charger (Port 2) Interrupt "
    "Unmasked\r\n"
    "[00:00:00.508,666] <inf> tps6699x: TPS6699X initialized\r\n"
    "[00:00:00.510,742] <inf> host_mux: Initial (Waiting for Host)\r\n"
    "*** Booting Zephyr OS build 4.4.2 ***\r\n"
    "[00:00:00.511,657] <inf> main: Maui Firmware Starting\r\n"
    "[00:00:00.598,052] <inf> fs_nvs: 8 Sectors of 4096 bytes\r\n"
    "[00:00:00.598,175] <inf> health: Boot health check: BBRAM crashloop_cnt=1 "
    "(raw=0x43525301)\r\n"
    "[00:00:00.598,571] <inf> adc: Initialized 8/8 ADC channels\r\n"
    "[00:00:00.598,907] <inf> usb_init: USB Device Serial Number: "
    "MAUIV12H26090104\r\n"
    "[00:00:00.602,508] <inf> host_mux: Connected Normal (CC1)\r\n"
)
PARTNER_VERSION_REPLY = (
    "version\r\n"
    "Maui version 0.69-89b053b (change-8465588, built 2026-10-16 07:18:53 UTC) "
    "[MCUBOOT-SLOT0 (0x80038200)]\r\n" + PARTNER_BOOT_LOG + "maui$ "
)
PARTNER_PDC_DUT = (
    "pdc status dut\r\n"
    "Plug Present:     Yes\r\n"
    "  Connection State: 3\r\n"
    "  Orientation:      Normal (CC1)\r\n"
    "  Power Role:       Source\r\n"
    "[00:00:41.120,003] <wrn> tps6699x: DUT (Port 1) status changed\r\n"
    "  Data Role:        UFP (Device)\r\n"
    "  Active Contract:\r\n"
    "    Type:           Fixed Supply\r\n"
    "    Voltage:        20.000 V\r\n"
    "    Max Current:    4.50 A\r\n"
    "    Op Current:     3.25 A\r\n"
    # A log message that interrupts a reply line.
    "  Measured VBUS:    [00:00:41.130,551] <inf> host_mux: Connected Normal (CC1)"
    "\r\n20.692 V (20692 mV)\r\n"
    "maui$ "
)
PARTNER_ADC_REPLY = (
    "adc status\r\nADC values:\r\n"
    "[        USB_HOST_CC1] =    5 mV\r\n"
    "\x1b[1;33m[00:00:42.000,100] <wrn> adc: channel 2 slow\x1b[0m\r\n"
    "[        USB_HOST_CC2] =  433 mV\r\n"
    "--- 3 messages dropped ---\r\n"
    "[            SBU1_DET] = 2997 mV\r\n"
    "[            SBU2_DET] =    5 mV\r\n"
    "[  PPVAR_USB_CHG_VBUS] = 20664 mV\r\n"
    "[  PPVAR_USB_DUT_VBUS] = 20720 mV\r\n"
    "[PP5000_USB_HOST_VBUS] = 2598 mV\r\n"
    "[              PP5000] = 2589 mV\r\n"
    "maui$ "
)


class FakeClock:
    """Monotonic test clock: sleep() and every GPIO read advance it."""

    def __init__(self):
        self.now = 1000.0

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class Timeline:
    """GPIO levels as a function of the time since the first read."""

    READ_S = 0.03

    def __init__(self, fake_clock, level_at, raw=None):
        self.clock = fake_clock
        self.level_at = level_at
        self.raw = raw
        self.start = None
        self.reads = 0

    def read(self):
        if self.start is None:
            self.start = self.clock.now
        t = self.clock.now - self.start
        self.clock.now += self.READ_S
        self.reads += 1
        level = self.level_at(t)
        raw = (
            self.raw
            if self.raw is not None
            else f"gpio get {GPIO}\r\n[            {GPIO}] I = {level}\r\nmaui$ "
        )
        return level, raw


def _press(start, end):
    """Released except for a press from |start| to |end| seconds."""
    return lambda t: 1 if start <= t < end else 0


def _run(level_at, raw=None):
    fake_clock = FakeClock()
    timeline = Timeline(fake_clock, level_at, raw)
    check = bsl.BslButtonCheck(
        timeline.read,
        is_missing=lambda raw: device.gpio_missing(raw, GPIO),
        clock=fake_clock.clock,
        sleep=fake_clock.sleep,
    )
    updates = list(check.run())
    return check, updates, timeline


class TestBslButtonCheck(unittest.TestCase):
    """The 3-phase check on a fake GPIO timeline."""

    def test_good_press_passes(self):
        check, updates, timeline = _run(_press(3.0, 5.0))
        self.assertTrue(check.success, check.message)
        self.assertTrue(updates[-1].done)
        self.assertTrue(updates[-1].success)
        self.assertEqual([p.passed for p in check.phases], [True, True, True])
        # Phase 1 takes about 2 s, i.e. about 17 samples at 0.12 s.
        self.assertGreaterEqual(len(check.phases[0].samples), 15)
        self.assertEqual({s.level for s in check.phases[0].samples}, {0})
        self.assertIn("1 after", check.phases[1].detail)
        # Sampling every 0.12 s (the read time is part of the interval).
        times = [s.t for p in check.phases for s in p.samples]
        gaps = {round(b - a, 2) for a, b in zip(times, times[1:])}
        self.assertEqual(gaps, {profile.BSL_POLL_INTERVAL_S})
        self.assertEqual(timeline.reads, len(times))

    def test_prompts_and_countdown(self):
        _, updates, _ = _run(_press(3.0, 5.0))
        prompts = [u.prompt for u in updates]
        for prompt in (
            bsl.PROMPT_IDLE,
            bsl.PROMPT_PRESS,
            bsl.PROMPT_HOLD,
            bsl.PROMPT_RELEASED,
        ):
            self.assertIn(prompt, prompts)
        self.assertEqual(
            [u.phase for u in updates if not u.done],
            sorted(u.phase for u in updates if not u.done),
        )
        press = [u.seconds_left for u in updates if u.prompt == bsl.PROMPT_PRESS]
        self.assertLessEqual(press[0], profile.BSL_PRESS_TIMEOUT_S)
        self.assertGreater(press[0], press[-1])
        idle = [u.seconds_left for u in updates if u.phase == bsl.PHASE_IDLE]
        self.assertLessEqual(max(idle), profile.BSL_IDLE_S)

    def test_floating_high_fails_phase_1(self):
        check, updates, timeline = _run(lambda t: 1)
        self.assertFalse(check.success)
        self.assertEqual(check.failed_phase, bsl.PHASE_IDLE)
        self.assertIn(
            "BSL signal reads 1 without pressing - R0714 missing or line floating?",
            check.message,
        )
        self.assertEqual(updates[-1].failed_phase, 1)
        self.assertEqual(timeline.reads, 1)

    def test_press_during_phase_1_fails(self):
        check, _, _ = _run(_press(1.0, 4.0))
        self.assertEqual(check.failed_phase, bsl.PHASE_IDLE)

    def test_no_press_fails_phase_2(self):
        check, _, timeline = _run(lambda t: 0)
        self.assertEqual(check.failed_phase, bsl.PHASE_PRESS)
        self.assertIn("no press detected within 10 s", check.message)
        elapsed = timeline.clock.now - timeline.start
        self.assertAlmostEqual(
            elapsed, profile.BSL_IDLE_S + profile.BSL_PRESS_TIMEOUT_S, delta=0.3
        )

    def test_unstable_press_fails_phase_2(self):
        # Bouncing: 0.5 s pressed, 0.5 s released, ...
        check, _, _ = _run(lambda t: 1 if t > 2.5 and int(t * 2) % 2 else 0)
        self.assertEqual(check.failed_phase, bsl.PHASE_PRESS)
        self.assertIn("signal not stable while pressed", check.message)

    def test_short_press_then_hold_passes(self):
        # A too short first press is retried within the press window.
        check, _, _ = _run(lambda t: 1 if 3.0 <= t < 3.5 or 5.0 <= t < 7.0 else 0)
        self.assertTrue(check.success, check.message)

    def test_press_started_before_the_timeout_may_finish_after_it(self):
        check, _, _ = _run(_press(11.5, 13.5))
        self.assertTrue(check.success, check.message)

    def test_stuck_after_release_fails_phase_3(self):
        check, _, timeline = _run(lambda t: 1 if t >= 3.0 else 0)
        self.assertEqual(check.failed_phase, bsl.PHASE_RELEASE)
        self.assertIn("signal stays 1 after release", check.message)
        self.assertEqual([p.passed for p in check.phases], [True, True, False])
        self.assertLess(timeline.clock.now - timeline.start, 3.0 + 1.5 + 5.0 + 0.5)

    def test_bouncing_release_fails_phase_3(self):
        check, _, _ = _run(lambda t: 1 if 3.0 <= t < 5.0 or int(t * 4) % 2 else 0)
        self.assertEqual(check.failed_phase, bsl.PHASE_IDLE)
        check, _, _ = _run(
            lambda t: 1 if 3.0 <= t < 5.0 or (t >= 5.0 and int(t * 2) % 2) else 0
        )
        self.assertEqual(check.failed_phase, bsl.PHASE_RELEASE)
        self.assertIn("signal not stable after release", check.message)

    def test_missing_gpio_fails_at_once(self):
        check, _, timeline = _run(lambda t: -1, raw=f"gpio get {GPIO}\r\nmaui$ ")
        self.assertEqual(check.failed_phase, bsl.PHASE_IDLE)
        self.assertIn(f"has no '{GPIO}' GPIO - update the MCU App", check.message)
        self.assertEqual(timeline.reads, 1)

    def test_unreadable_gpio_fails(self):
        check, _, _ = _run(lambda t: -1, raw="")
        self.assertEqual(check.failed_phase, bsl.PHASE_IDLE)
        self.assertIn("could not read GPIO", check.message)

    def test_summary(self):
        check, _, _ = _run(lambda t: 1 if t >= 3.0 else 0)
        summary = check.summary()
        self.assertIn("every 0.12 s in one console session", summary)
        self.assertIn("Step 1 (Do not touch): PASS", summary)
        self.assertIn("Step 2 (Press and hold): PASS", summary)
        self.assertIn("Step 3 (Release): FAIL - signal stays 1 after release", summary)
        self.assertRegex(summary, r"Samples: 0x\d+ \[0\.00-")
        check, _, _ = _run(lambda t: 1)
        summary = check.summary()
        self.assertIn("Step 2 (Press and hold): NOT RUN", summary)
        self.assertIn("Step 3 (Release): NOT RUN", summary)

    def test_run_lengths(self):
        samples = [
            bsl.BslSample(0.0, 0),
            bsl.BslSample(0.12, 0),
            bsl.BslSample(0.24, 1),
        ]
        samples.append(bsl.BslSample(0.36, -1))
        self.assertEqual(
            bsl.run_lengths(samples),
            "0x2 [0.00-0.12 s], 1x1 [0.24-0.24 s], ?x1 [0.36-0.36 s]",
        )


class FakeTester:
    """MauiV12ConsoleTester stand-in reading a Timeline."""

    def __init__(self, timeline):
        self.timeline = timeline
        self.sessions = 0
        self.sers = set()

    @contextlib.contextmanager
    def session(self):
        self.sessions += 1
        yield f"ser-{self.sessions}"

    def read_gpio(self, name, ser=None):
        assert name == GPIO
        self.sers.add(ser)
        return self.timeline.read()


@mock.patch.object(maui_v12_services, "write_device_log")
class TestBslServicer(unittest.TestCase):
    """stream_bsl_button_test / test_bsl_button of the v1.2 servicer."""

    def _servicer(self, level_at, raw=None):
        servicer = maui_v12_services.MauiV12Station2Servicer()
        fake_clock = FakeClock()
        servicer._clock = fake_clock.clock
        servicer._sleep = fake_clock.sleep
        self.tester = FakeTester(Timeline(fake_clock, level_at, raw))
        servicer._make_console_tester = lambda port: self.tester
        servicer._fallback_console_port = lambda port: "/dev/ttyACM0"
        return servicer

    def _request(self):
        return maui_manufacturing_pb2.MauiBslButtonTestRequest(
            device_port="/dev/ttyACM0", serial_number=SN
        )

    def test_stream_good_press(self, mock_log):
        servicer = self._servicer(_press(3.0, 5.0))
        updates = list(servicer.stream_bsl_button_test(self._request(), None))
        self.assertGreater(len(updates), 30)
        self.assertEqual(
            {u.phase for u in updates[:-1]}, {1, 2, 3}, [u.phase for u in updates]
        )
        self.assertEqual([u.done for u in updates].count(True), 1)
        self.assertTrue(updates[-1].done and updates[-1].success)
        # All samples in one console session.
        self.assertEqual(self.tester.sessions, 1)
        self.assertEqual(self.tester.sers, {"ser-1"})
        mock_log.assert_called_once()
        serial_no, step, content = mock_log.call_args[0]
        self.assertEqual((serial_no, step), (SN, "bsl_button_test"))
        self.assertTrue(content.startswith("ALL_PASSED=True\nRESULT: PASSED\n"))
        for step_no in (1, 2, 3):
            self.assertIn(f"Step {step_no} (", content)
        self.assertIn("Last raw GPIO output:", content)

    def test_stream_floating_high(self, mock_log):
        servicer = self._servicer(lambda t: 1)
        updates = list(servicer.stream_bsl_button_test(self._request(), None))
        self.assertEqual(len(updates), 1)
        final = updates[0]
        self.assertTrue(final.done)
        self.assertFalse(final.success)
        self.assertEqual(final.failed_phase, 1)
        self.assertIn("R0714 missing or line floating?", final.message)
        self.assertIn("ALL_PASSED=False", mock_log.call_args[0][2])
        self.assertIn("RESULT: FAILED", mock_log.call_args[0][2])

    def test_console_error(self, mock_log):
        servicer = self._servicer(lambda t: 0)
        tester = mock.MagicMock()
        tester.session.side_effect = OSError("No such device")
        servicer._make_console_tester = lambda port: tester
        updates = list(servicer.stream_bsl_button_test(self._request(), None))
        self.assertEqual(len(updates), 1)
        self.assertTrue(updates[0].done)
        self.assertFalse(updates[0].success)
        self.assertIn("No such device", updates[0].message)
        self.assertIn("ALL_PASSED=False", mock_log.call_args[0][2])

    def test_unary_returns_the_verdict(self, unused_log):
        servicer = self._servicer(lambda t: 0)
        resp = servicer.test_bsl_button(self._request(), None)
        self.assertFalse(resp.success)
        self.assertIn("no press detected within 10 s", resp.message)
        servicer = self._servicer(_press(2.5, 4.5))
        resp = servicer.test_bsl_button(self._request(), None)
        self.assertTrue(resp.success, resp.message)
        self.assertIn("PASSED", resp.message)


class FakeSerial:
    """Serial port: |pending| chunks arrive before the command, then |reply|."""

    def __init__(self, reply, pending=()):
        self.reply = reply.encode()
        self.pending = [p.encode() for p in pending]
        self.buffer = b""
        self.written = []

    @property
    def in_waiting(self):
        if not self.buffer and self.pending:
            self.buffer = self.pending.pop(0)
        return len(self.buffer)

    def read(self, size=1):
        data, self.buffer = self.buffer[:size], self.buffer[size:]
        return data

    def reset_input_buffer(self):
        self.buffer = b""

    def reset_output_buffer(self):
        pass

    def write(self, data):
        self.written.append(data)
        # Nothing of the stale output may be left when the command is sent.
        assert not self.pending, self.pending
        self.buffer = self.reply

    def flush(self):
        pass


class TestConsoleCleanUp(unittest.TestCase):
    """Drain + clean-up of the v1.2 console with real partner transcripts."""

    def setUp(self):
        self.tester = device.MauiV12ConsoleTester("/dev/ttyACM0")

    def test_clean_console_output(self):
        self.assertEqual(
            device.clean_console_output(PARTNER_BSL_REPLY),
            "maui$ gpio get ec_bsl_invoke\r\n[            ec_bsl_invoke] I = 1\r\n"
            "maui$ ",
        )
        cleaned = device.clean_console_output(PARTNER_VERSION_REPLY)
        self.assertNotIn("<inf>", cleaned)
        self.assertNotIn("messages dropped", cleaned)
        self.assertNotIn("Booting Zephyr", cleaned)
        self.assertTrue(cleaned.endswith("[MCUBOOT-SLOT0 (0x80038200)]\r\nmaui$ "))
        self.assertEqual(
            device.clean_console_output("\x1b[1;32mmaui$ \x1b[m"), "maui$ "
        )
        # Lines that only look similar stay.
        text = "[  PP5000] = 2589 mV\r\n--- note ---\r\n"
        self.assertEqual(device.clean_console_output(text), text)

    def test_pending_boot_output_is_drained_before_the_command(self):
        chunks = PARTNER_BOOT_LOG.splitlines(keepends=True)
        ser = FakeSerial(
            f"gpio get {GPIO}\r\n[            {GPIO}] I = 0\r\nmaui$ ", chunks
        )
        level, raw = self.tester.read_gpio(GPIO, ser=ser)
        self.assertEqual(level, 0)
        self.assertNotIn("<inf>", raw)
        self.assertEqual(ser.written, [f"gpio get {GPIO}\r\n".encode()])

    def test_drain_stops_after_the_max_time(self):
        fake_clock = FakeClock()
        ser = mock.MagicMock()
        type(ser).in_waiting = mock.PropertyMock(return_value=4)

        def read(size):
            fake_clock.now += 0.01
            return b"x" * size

        ser.read.side_effect = read
        drained = device.drain_input(
            ser, clock=fake_clock.clock, sleep=fake_clock.sleep
        )
        self.assertGreaterEqual(len(drained), 4 * 49)
        self.assertLessEqual(
            fake_clock.now - 1000.0, profile.CONSOLE_DRAIN_MAX_S + 0.02
        )

    def test_drain_ignores_port_errors(self):
        ser = mock.MagicMock()
        type(ser).in_waiting = mock.PropertyMock(side_effect=OSError("gone"))
        self.assertEqual(device.drain_input(ser), b"")

    def test_partner_bsl_reply(self):
        level, raw = self.tester.read_gpio(GPIO, ser=FakeSerial(PARTNER_BSL_REPLY))
        self.assertEqual(level, 1)
        self.assertNotIn("messages dropped", raw)

    def test_partner_version_reply(self):
        out = self.tester.send_command(FakeSerial(PARTNER_VERSION_REPLY), "version")
        check = profile.check_mcu_version(out)
        self.assertIn("0.69-89b053b", check.message)
        self.assertNotIn("<inf>", out)

    def test_pdc_status_with_log_lines(self):
        res = self.tester.read_pdc_port_status("dut", ser=FakeSerial(PARTNER_PDC_DUT))
        self.assertTrue(res["plug_present"])
        self.assertEqual(res["data_role"], "UFP (Device)")
        self.assertEqual(res["voltage_v"], 20.0)
        self.assertEqual(res["measured_vbus_mv"], 20692)
        self.assertNotIn("<wrn>", res["raw_output"])

    def test_adc_status_with_log_lines(self):
        res = self.tester.read_adc_status(ser=FakeSerial(PARTNER_ADC_REPLY))
        self.assertEqual(res["host_cc2_mv"], 433)
        self.assertEqual(res["pp5000_mv"], 2589)
        channels = device.parse_adc_channels(res["raw_output"])
        self.assertEqual(set(channels), set(device.ADC_CHANNELS))
        mismatches = device.verify_host_adc(res)[1]
        self.assertFalse([m for m in mismatches if "missing" in m or "error" in m])


if __name__ == "__main__":
    unittest.main()
