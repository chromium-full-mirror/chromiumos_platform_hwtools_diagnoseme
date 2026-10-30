// Copyright 2026 The ChromiumOS Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

import {
  CODE128_PATTERNS,
  UnsupportedBarcodeCharacterError,
  encodeCode128B,
  findUnsupportedCode128BChars,
  renderCode128B,
} from './code128';

// Golden Code 128-B encoding of a Maui-style serial number.
const GOLDEN_SERIAL = 'MAUI-0001';
const GOLDEN_CODES = [104, 45, 33, 53, 41, 13, 16, 16, 16, 17, 62, 106];
const GOLDEN_MODULES =
  '211214' + // START B
  '113123' + // M
  '111323' + // A
  '213131' + // U
  '231311' + // I
  '122132' + // -
  '123122' + // 0
  '123122' + // 0
  '123122' + // 0
  '123221' + // 1
  '431111' + // checksum 62
  '2331112'; // STOP

describe('Code 128-B encoder', () => {
  it('has a complete symbol table', () => {
    expect(CODE128_PATTERNS.length).toBe(107);
    CODE128_PATTERNS.forEach((pattern, value) => {
      const modules = [...pattern].reduce((sum, d) => sum + Number(d), 0);
      expect(modules)
        .withContext(`symbol ${value}`)
        .toBe(value === 106 ? 13 : 11);
    });
  });

  it('encodes a known serial (golden)', () => {
    expect(encodeCode128B(GOLDEN_SERIAL)).toEqual(GOLDEN_CODES);
    expect(GOLDEN_CODES.map(c => CODE128_PATTERNS[c]).join('')).toBe(
      GOLDEN_MODULES,
    );
  });

  it('lays out the golden bars', () => {
    const moduleWidth = 2;
    const quietZone = 20;
    const barcode = renderCode128B(GOLDEN_SERIAL, moduleWidth, quietZone);

    const expectedBarWidths = [...GOLDEN_MODULES]
      .filter((_, i) => i % 2 === 0)
      .map(d => Number(d) * moduleWidth);
    expect(barcode.bars.map(b => b.width)).toEqual(expectedBarWidths);
    expect(barcode.bars[0].x).toBe(quietZone);
    // 11 symbols of 11 modules + 13-module STOP, plus both quiet zones.
    expect(barcode.totalWidth).toBe(
      (11 * 11 + 13) * moduleWidth + 2 * quietZone,
    );
  });

  it('rejects characters outside printable ASCII', () => {
    expect(findUnsupportedCode128BChars('MAUI\t0é0é')).toEqual(['\t', 'é']);
    expect(() => encodeCode128B('MAUI-é')).toThrowError(
      UnsupportedBarcodeCharacterError,
    );
    expect(() => renderCode128B('MAUI\n1', 2, 20)).toThrowError(
      UnsupportedBarcodeCharacterError,
    );
  });

  it('accepts the whole printable ASCII range', () => {
    let all = '';
    for (let c = 32; c <= 126; c++) {
      all += String.fromCharCode(c);
    }
    expect(findUnsupportedCode128BChars(all)).toEqual([]);
    expect(encodeCode128B(all).length).toBe(all.length + 3);
  });
});
