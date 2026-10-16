// Copyright 2026 The ChromiumOS Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

import {
  ChangeDetectorRef,
  Component,
  OnDestroy,
  OnInit,
  ViewChild,
  inject,
} from '@angular/core';
import {CommonModule} from '@angular/common';
import {FormsModule} from '@angular/forms';
import {MatButtonModule} from '@angular/material/button';
import {MatIconModule} from '@angular/material/icon';
import {MatTooltipModule} from '@angular/material/tooltip';
import {EMPTY, Subscription, timer} from 'rxjs';
import {catchError, exhaustMap} from 'rxjs/operators';
import {DiagnosemeReworkCard} from '../diagnoseme-rework-card/diagnoseme-rework-card';
import {DiagnosemeReworkCardColumn} from '../diagnoseme-rework-card-column/diagnoseme-rework-card-column';
import {MauiDeviceMgmtService} from '../services/maui-device-mgmt.service';
import {TestPassFailButton} from '../test-pass-fail-button/test-pass-fail-button';

const CODE128_PATTERNS: string[] = [
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

export interface BarcodeBar {
  x: number;
  width: number;
}

@Component({
  selector: 'app-maui-step3-labeling',
  standalone: true,
  templateUrl: './maui-step3-labeling.html',
  styleUrl: './maui-step3-labeling.scss',
  imports: [
    CommonModule,
    DiagnosemeReworkCard,
    DiagnosemeReworkCardColumn,
    FormsModule,
    MatButtonModule,
    MatIconModule,
    MatTooltipModule,
    TestPassFailButton,
  ],
})
export class MauiStep3LabelingComponent implements OnInit, OnDestroy {
  private readonly deviceMgmtService = inject(MauiDeviceMgmtService);
  private readonly cdr = inject(ChangeDetectorRef);

  private readonly subs = new Subscription();
  private pollSub?: Subscription;

  electronicSerial = '';
  adbSerial = '';
  adbConnected = false;
  ftdiSerial = '';
  ftdiConnected = false;
  dualEndpointsMatch = false;
  deviceConnected = false;
  ftdiIsProgrammed = false;
  readingSerial = false;
  serialDetectionMessage = 'Scanning USB bus for connected Maui device...';
  copySuccessMessage = '';

  // Code 128 SVG Barcode rendering data
  svgBarcodeBars: BarcodeBar[] = [];
  barcodeTotalWidth = 280;
  barcodeHeight = 85;

  @ViewChild('readoutStatus') readoutStatus?: TestPassFailButton;

  ngOnInit(): void {
    this.pollElectronicSerial();
  }

  ngOnDestroy(): void {
    this.pollSub?.unsubscribe();
    this.subs.unsubscribe();
  }

  pollElectronicSerial(): void {
    // Restart (never stack) the polling loop, e.g. on "Refresh Detection".
    this.pollSub?.unsubscribe();
    this.pollSub = timer(0, 2000)
      .pipe(
        exhaustMap(() => {
          this.readingSerial = true;
          return this.deviceMgmtService.readConnectedDeviceSerial().pipe(
            catchError(() => {
              this.readingSerial = false;
              this.serialDetectionMessage = 'Error querying device detection.';
              this.cdr.detectChanges();
              return EMPTY;
            }),
          );
        }),
      )
      .subscribe(res => {
        this.readingSerial = false;
        this.adbSerial = res.adbSerial || '';
        this.adbConnected = res.adbConnected;
        this.ftdiSerial = res.ftdiSerial || '';
        this.ftdiConnected = res.ftdiConnected;
        this.dualEndpointsMatch = res.dualEndpointsMatch;
        this.deviceConnected = res.isConnected;
        this.ftdiIsProgrammed = !!res.ftdiIsProgrammed;
        const detectedSn = (
          res.serialNumber ||
          res.adbSerial ||
          res.ftdiSerial ||
          ''
        ).trim();

        if (res.isConnected && detectedSn) {
          this.electronicSerial = detectedSn;
          this.serialDetectionMessage = `Connected Maui Device: ${this.electronicSerial}`;
          this.generateBarcode(this.electronicSerial);
          this.readoutStatus?.showPassed();
        } else {
          this.electronicSerial = '';
          this.svgBarcodeBars = [];
          this.serialDetectionMessage =
            'No Maui Device Detected. Please connect Maui Host USB-C cable.';
          this.readoutStatus?.showFailed();
        }
        this.cdr.detectChanges();
      });
  }

  generateBarcode(text: string): void {
    if (!text) {
      this.svgBarcodeBars = [];
      return;
    }

    const cleanText = text.trim();
    const moduleWidth = 2;
    const quietZone = 20;
    const codes: number[] = [];

    // Start Code B is index 104
    codes.push(104);

    for (let i = 0; i < cleanText.length; i++) {
      const charCode = cleanText.charCodeAt(i);
      const val = charCode >= 32 && charCode <= 126 ? charCode - 32 : 0;
      codes.push(val);
    }

    // Calculate Code 128 Checksum: (104 + sum((i + 1) * code_i)) % 103
    let checksum = 104;
    for (let i = 1; i < codes.length; i++) {
      checksum += i * codes[i];
    }
    codes.push(checksum % 103);

    // Stop Code is index 106
    codes.push(106);

    const bars: BarcodeBar[] = [];
    let currentX = quietZone;

    for (let cIdx = 0; cIdx < codes.length; cIdx++) {
      const pattern = CODE128_PATTERNS[codes[cIdx]] || '212222';
      for (let pIdx = 0; pIdx < pattern.length; pIdx++) {
        const width = parseInt(pattern[pIdx], 10) * moduleWidth;
        const isBar = pIdx % 2 === 0;
        if (isBar) {
          bars.push({x: currentX, width});
        }
        currentX += width;
      }
    }

    // Total width including right quiet zone
    this.barcodeTotalWidth = currentX + quietZone;
    this.svgBarcodeBars = bars;
  }

  copySerialToClipboard(): void {
    if (!this.electronicSerial) {
      return;
    }
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard
        .writeText(this.electronicSerial)
        .then(() => {
          this.copySuccessMessage = 'Serial copied to clipboard!';
          setTimeout(() => {
            this.copySuccessMessage = '';
            this.cdr.detectChanges();
          }, 3000);
          this.cdr.detectChanges();
        })
        .catch(() => {
          this.copySuccessMessage = `Serial: ${this.electronicSerial}`;
          this.cdr.detectChanges();
        });
    } else {
      this.copySuccessMessage = `Serial: ${this.electronicSerial}`;
      this.cdr.detectChanges();
    }
  }
}
