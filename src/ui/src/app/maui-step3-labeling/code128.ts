// Copyright 2026 The ChromiumOS Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

/**
 * Minimal Code 128 (code set B) encoder for the on-screen serial barcode.
 */

/** Bar/space module widths of the 107 Code 128 symbols (index = value). */
export const CODE128_PATTERNS: readonly string[] = [
  '212222',
  '222122',
  '222221',
  '121223',
  '121322',
  '131222',
  '122213',
  '122312',
  '132212',
  '221213',
  '221312',
  '231212',
  '112232',
  '122132',
  '122231',
  '113222',
  '123122',
  '123221',
  '223211',
  '221132',
  '221231',
  '213212',
  '223112',
  '312131',
  '311222',
  '321122',
  '321221',
  '312212',
  '322112',
  '322211',
  '212123',
  '212321',
  '232121',
  '111323',
  '131123',
  '131321',
  '112313',
  '132113',
  '132311',
  '211313',
  '231113',
  '231311',
  '112133',
  '112331',
  '132131',
  '113123',
  '113321',
  '133121',
  '313121',
  '211331',
  '231131',
  '213113',
  '213311',
  '213131',
  '311123',
  '311321',
  '331121',
  '312113',
  '312311',
  '332111',
  '314111',
  '221411',
  '431111',
  '111224',
  '111422',
  '121124',
  '121421',
  '141122',
  '141221',
  '112214',
  '112412',
  '122114',
  '122411',
  '142112',
  '142211',
  '241211',
  '221114',
  '413111',
  '241112',
  '134111',
  '111242',
  '121142',
  '121241',
  '114212',
  '124112',
  '124211',
  '411212',
  '421112',
  '421211',
  '212141',
  '214121',
  '412121',
  '111143',
  '111341',
  '131141',
  '114113',
  '114311',
  '411113',
  '411311',
  '113141',
  '114131',
  '311141',
  '411131',
  '211412',
  '211214',
  '211232',
  '2331112',
];

/** Code 128 symbol values used by the encoder. */
export const CODE128_START_B = 104;
export const CODE128_STOP = 106;
const CODE128_CHECKSUM_MODULO = 103;

/** Code set B encodes printable ASCII (space to '~'). */
const CODE_B_FIRST_CHAR = 32;
const CODE_B_LAST_CHAR = 126;

export interface BarcodeBar {
  x: number;
  width: number;
}

export interface Code128Barcode {
  bars: BarcodeBar[];
  /** Total width including both quiet zones. */
  totalWidth: number;
}

/** Thrown when the text contains characters that code set B cannot encode. */
export class UnsupportedBarcodeCharacterError extends Error {
  constructor(readonly unsupportedChars: string[]) {
    super(
      `Cannot encode ${unsupportedChars
        .map(c => JSON.stringify(c))
        .join(', ')} in a Code 128-B barcode.`,
    );
    this.name = 'UnsupportedBarcodeCharacterError';
  }
}

/** Returns the distinct characters of `text` that code set B cannot encode. */
export function findUnsupportedCode128BChars(text: string): string[] {
  const unsupported = new Set<string>();
  for (const ch of text) {
    const code = ch.charCodeAt(0);
    if (ch.length > 1 || code < CODE_B_FIRST_CHAR || code > CODE_B_LAST_CHAR) {
      unsupported.add(ch);
    }
  }
  return [...unsupported];
}

/**
 * Encodes `text` into Code 128 symbol values: START B, data, checksum, STOP.
 *
 * @throws UnsupportedBarcodeCharacterError for characters outside ASCII 32-126.
 */
export function encodeCode128B(text: string): number[] {
  const unsupported = findUnsupportedCode128BChars(text);
  if (unsupported.length > 0) {
    throw new UnsupportedBarcodeCharacterError(unsupported);
  }
  const codes = [CODE128_START_B];
  for (let i = 0; i < text.length; i++) {
    codes.push(text.charCodeAt(i) - CODE_B_FIRST_CHAR);
  }
  // Checksum: (START + sum(position * value)) % 103
  let checksum = CODE128_START_B;
  for (let i = 1; i < codes.length; i++) {
    checksum += i * codes[i];
  }
  codes.push(checksum % CODE128_CHECKSUM_MODULO);
  codes.push(CODE128_STOP);
  return codes;
}

/**
 * Lays out the bars of the Code 128-B barcode of `text`.
 *
 * @throws UnsupportedBarcodeCharacterError for characters outside ASCII 32-126.
 */
export function renderCode128B(
  text: string,
  moduleWidth: number,
  quietZone: number,
): Code128Barcode {
  const bars: BarcodeBar[] = [];
  let currentX = quietZone;
  for (const code of encodeCode128B(text)) {
    const pattern = CODE128_PATTERNS[code];
    for (let i = 0; i < pattern.length; i++) {
      const width = parseInt(pattern[i], 10) * moduleWidth;
      // Even positions are bars, odd positions are spaces.
      if (i % 2 === 0) {
        bars.push({x: currentX, width});
      }
      currentX += width;
    }
  }
  return {bars, totalWidth: currentX + quietZone};
}
