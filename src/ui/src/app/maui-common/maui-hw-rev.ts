// Copyright 2026 The ChromiumOS Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

/** Maui hardware revisions supported by the shared Maui pages. */
export type MauiHwRev = 'v1.1' | 'v1.2';

/**
 * Revision specific texts and behaviour of the shared Maui pages.
 *
 * The backend of each edition already talks to the right hardware; this
 * profile only adapts what the operator sees and which cards are shown.
 */
export interface MauiHwRevProfile {
  rev: MauiHwRev;
  /** Name of the MCU console USB endpoint (ftdi_* fields of the protos). */
  consoleLabel: string;
  /** Console node shown when the backend did not report one. */
  defaultConsolePort: string;
  /**
   * Expected `hwrev` shown next to the reading when the backend did not
   * report it (the server's expected_hwrev is the source of truth).
   */
  expectedHwrevText: string;
  /** BSL button readout: v1.1 BSL ADC channel, v1.2 ec_bsl_invoke GPIO. */
  bslMethod: 'adc' | 'gpio';
  /** v1.2 copies the H2H serial into the MCU before the tests. */
  requiresSerialProvisioning: boolean;
  /** v1.2 reads the serial from the H2H SPI flash before the tests. */
  requiresH2hSpiCheck: boolean;
  /**
   * Added to the numbers of the functional cards: v1.2 starts with the
   * serial provisioning ("1.") and H2H SPI ("2.") cards, so its Host USB card
   * is "3.".
   */
  cardNumberOffset: number;
  /** Hint shown for a connected but unprovisioned board. */
  unprovisionedHint: string;
  /** Formats the console PID shown for a detected board. */
  consolePidText: (isProgrammed: boolean) => string;
  /** GPIO row text of a passing GPIO check (|details| from the backend). */
  gpioOkText: (details: string) => string;
  /** Texts of the visual LED card. */
  leds: MauiLedTexts;
}

/** What the operator must see on the two LEDs of the board. */
export interface MauiLedTexts {
  amberName: string;
  amberInstruction: string;
  amberCheckLabel: string;
  greenName: string;
  greenInstruction: string;
  greenCheckLabel: string;
  /** Shown when Confirm is clicked without both boxes checked. */
  incompleteMessage: string;
}

export const MAUI_HW_REV_PROFILES: Record<MauiHwRev, MauiHwRevProfile> = {
  'v1.1': {
    rev: 'v1.1',
    consoleLabel: 'FTDI',
    defaultConsolePort: '/dev/ttyUSB0',
    expectedHwrevText: '2',
    bslMethod: 'adc',
    requiresSerialProvisioning: false,
    requiresH2hSpiCheck: false,
    cardNumberOffset: 0,
    unprovisionedHint: 'Step 1 provisioning required before functional testing',
    consolePidText: isProgrammed => (isProgrammed ? '0x6002' : '0x6001'),
    gpioOkText: () => '19/19 Verified',
    leds: {
      amberName: 'Amber LED (PD Contract)',
      amberInstruction: 'Must be ON when charger plugged.',
      amberCheckLabel: 'Amber LED (PD Contract) ON',
      greenName: 'Green LED (Host Link)',
      greenInstruction: 'Must be BLINKING at 1 Hz (500ms ON / 500ms OFF).',
      greenCheckLabel: 'Green LED (Host Link) Blinking at 1 Hz',
      incompleteMessage:
        'Please visually inspect and confirm Amber LED is ON and Green LED is BLINKING at 1 Hz.',
    },
  },
  'v1.2': {
    rev: 'v1.2',
    consoleLabel: 'MCU',
    defaultConsolePort: '/dev/ttyACM0',
    expectedHwrevText: '3',
    bslMethod: 'gpio',
    requiresSerialProvisioning: true,
    requiresH2hSpiCheck: true,
    cardNumberOffset: 2,
    unprovisionedHint:
      'MCU serial not provisioned yet (run the Serial Number card)',
    consolePidText: () => '0x5085',
    gpioOkText: details => details || 'Verified',
    leds: {
      amberName: 'Amber LED (Charger)',
      amberInstruction:
        'Must be SOLID ON while the charger has a PD contract (charger plugged in, no DUT).',
      amberCheckLabel: 'Amber LED (Charger PD Contract) solid ON',
      greenName: 'Green LED (MCU App)',
      greenInstruction:
        'Must be BLINKING at about 1 Hz while the MCU App runs (500 ms ON / 500 ms OFF).',
      greenCheckLabel: 'Green LED (MCU App) blinking at ~1 Hz',
      incompleteMessage:
        'Please confirm that the Amber LED is solid ON with the charger plugged in and that the Green LED blinks at about 1 Hz.',
    },
  },
};

/** Returns the profile of |rev|, defaulting to v1.1. */
export function mauiHwRevProfile(rev: MauiHwRev | undefined): MauiHwRevProfile {
  return MAUI_HW_REV_PROFILES[rev ?? 'v1.1'] ?? MAUI_HW_REV_PROFILES['v1.1'];
}
