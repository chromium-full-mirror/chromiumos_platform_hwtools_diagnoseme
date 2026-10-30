// Copyright 2026 The ChromiumOS Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

/**
 * Shared constants for the Maui manufacturing Steps (1-4).
 *
 * Timings and thresholds used to be scattered as magic numbers across the
 * Step components; keep them here so that factory-tuned values live in one
 * place.
 */

/** Result of a single test card / provisioning step. */
export type MauiStepStatus = 'NOT_RUN' | 'PASSED' | 'FAILED' | 'BYPASSED';

/** Operator ID reported to MES until stations get operator login. */
export const MAUI_OPERATOR_ID = 'OP_FACTORY';

/** Station IDs reported to MES (the backend derives result folders from them). */
export const MAUI_STATION_ID = {
  smtStep1: 'STATION_SMT_STEP1',
  smtFunctional: 'STATION_SMT_FUNCTIONAL',
  fatpQt0: 'STATION_FATP_QT0',
} as const;

/** Firmware channel used for MCU and PDC flashing. */
export const MAUI_FW_CHANNEL = 'stable';

/** Station directory where the backend exports final result packages. */
export const MAUI_RESULTS_DIR = '/var/log/diagnoseme/maui_v11_results';
/** Same for Maui v1.2 (see MauiHwRevProfile.resultsDir). */
export const MAUI_V12_RESULTS_DIR = '/var/log/diagnoseme/maui_v12_results';

// FTDI USB identifiers.
export const FTDI_VID = 0x0403;
export const FTDI_PID_UNPROGRAMMED = 0x6001;
export const FTDI_PID_PROGRAMMED = 0x6002;
export const FTDI_PRODUCT_NAME = 'Maui Serial Console';

// Polling intervals.
export const DEVICE_POLL_INTERVAL_MS = 1500;
export const DISCONNECT_POLL_INTERVAL_MS = 1500;
export const STEP3_SERIAL_POLL_INTERVAL_MS = 2000;

// Delays.
/** Delay between the operator click and the test RPC (cable/USB settling). */
export const STABILIZATION_DELAY_MS = 3000;
/** Delay after MCU flashing for firmware boot and USB 3.0 MUX settling. */
export const MCU_SETTLE_DELAY_MS = 6000;
/** Delay before reloading the page for the next unit/batch. */
export const PAGE_RELOAD_DELAY_MS = 2500;
/** Delay before reconnecting the BSL auto-discovery stream after an error. */
export const BSL_DISCOVERY_RETRY_MS = 3000;
/** How long the "copied to clipboard" message stays visible. */
export const COPY_MESSAGE_TIMEOUT_MS = 3000;

// USB handheld barcode scanner detection (keyboard wedge).
/** Max gap between key presses that still counts as one scanner burst. */
export const BARCODE_SCANNER_KEY_GAP_MS = 100;
/** Minimum length of a scanned barcode. */
export const BARCODE_MIN_LENGTH = 4;

// RESET button test.
/** Baseline uptime assumed when the operator bypasses the baseline capture. */
export const RESET_BYPASS_BASELINE_UPTIME_MS = 15000;
/** Baseline uptime sent when no baseline was captured. */
export const RESET_DEFAULT_BASELINE_UPTIME_MS = 10000;

// Display-only expectations. The backend owns the actual pass/fail limits
// (see maui_console_tester.py); keep these in sync with it.
export const EXPECTED_HWREV = 2;
export const TEMPERATURE_RANGE_LABEL = '20–65°C';
export const EXPECTED_GPIO_COUNT = 19;
export const BSL_THRESHOLD_MV = 2500;
