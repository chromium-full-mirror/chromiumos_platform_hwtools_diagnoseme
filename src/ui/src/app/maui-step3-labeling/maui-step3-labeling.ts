// Copyright 2026 The ChromiumOS Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

import {
  ChangeDetectorRef,
  Component,
  DestroyRef,
  Input,
  OnInit,
  ViewChild,
  inject,
} from '@angular/core';
import {takeUntilDestroyed} from '@angular/core/rxjs-interop';
import {CommonModule} from '@angular/common';
import {FormsModule} from '@angular/forms';
import {MatButtonModule} from '@angular/material/button';
import {MatIconModule} from '@angular/material/icon';
import {MatTooltipModule} from '@angular/material/tooltip';
import {EMPTY, Subject, catchError, exhaustMap, merge, timer} from 'rxjs';
import {DiagnosemeReworkCard} from '../diagnoseme-rework-card/diagnoseme-rework-card';
import {DiagnosemeReworkCardColumn} from '../diagnoseme-rework-card-column/diagnoseme-rework-card-column';
import {
  COPY_MESSAGE_TIMEOUT_MS,
  STEP3_SERIAL_POLL_INTERVAL_MS,
} from '../maui-common/maui-constants';
import {MauiDeviceMgmtService} from '../services/maui-device-mgmt.service';
import {TestPassFailButton} from '../test-pass-fail-button/test-pass-fail-button';
import {
  MauiHwRev,
  MauiHwRevProfile,
  mauiHwRevProfile,
} from '../maui-common/maui-hw-rev';
import {
  BarcodeBar,
  UnsupportedBarcodeCharacterError,
  renderCode128B,
} from './code128';

/** Width of one narrow Code 128 module in SVG units. */
const BARCODE_MODULE_WIDTH = 2;
/** Blank margin left and right of the barcode in SVG units. */
const BARCODE_QUIET_ZONE = 20;

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
export class MauiStep3LabelingComponent implements OnInit {
  /** Hardware revision of the board (endpoint names in the readout). */
  @Input() hwRev: MauiHwRev = 'v1.1';
  @Input() pageTitle =
    'Maui FATP Step 3: Serial Number Readout & Label Printing Assistant';

  get rev(): MauiHwRevProfile {
    return mauiHwRevProfile(this.hwRev);
  }

  private readonly deviceMgmtService = inject(MauiDeviceMgmtService);
  private readonly cdr = inject(ChangeDetectorRef);
  private readonly destroyRef = inject(DestroyRef);

  /** Triggers an immediate detection in addition to the regular polling. */
  private readonly refresh$ = new Subject<void>();

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
  /** Set when the serial cannot be encoded as a Code 128-B barcode. */
  barcodeError = '';

  @ViewChild('readoutStatus') readoutStatus?: TestPassFailButton;

  ngOnInit(): void {
    this.pollElectronicSerial();
  }

  private pollElectronicSerial(): void {
    merge(timer(0, STEP3_SERIAL_POLL_INTERVAL_MS), this.refresh$)
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
        takeUntilDestroyed(this.destroyRef),
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
          if (this.barcodeError) {
            this.readoutStatus?.showFailed();
          } else {
            this.readoutStatus?.showPassed();
          }
        } else {
          this.electronicSerial = '';
          this.svgBarcodeBars = [];
          this.barcodeError = '';
          this.serialDetectionMessage =
            'No Maui Device Detected. Please connect Maui Host USB-C cable.';
          this.readoutStatus?.showFailed();
        }
        this.cdr.detectChanges();
      });
  }

  /** Re-reads the connected device now (Refresh buttons). */
  refreshSerial(): void {
    this.refresh$.next();
  }

  generateBarcode(text: string): void {
    this.barcodeError = '';
    if (!text) {
      this.svgBarcodeBars = [];
      return;
    }
    try {
      const barcode = renderCode128B(
        text.trim(),
        BARCODE_MODULE_WIDTH,
        BARCODE_QUIET_ZONE,
      );
      this.barcodeTotalWidth = barcode.totalWidth;
      this.svgBarcodeBars = barcode.bars;
    } catch (err) {
      if (!(err instanceof UnsupportedBarcodeCharacterError)) {
        throw err;
      }
      // Never show a scannable barcode that encodes a different serial.
      this.svgBarcodeBars = [];
      this.barcodeError = err.message;
    }
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
          timer(COPY_MESSAGE_TIMEOUT_MS)
            .pipe(takeUntilDestroyed(this.destroyRef))
            .subscribe(() => {
              this.copySuccessMessage = '';
              this.cdr.detectChanges();
            });
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
