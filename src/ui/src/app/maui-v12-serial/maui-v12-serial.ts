// Copyright 2026 The ChromiumOS Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

import {
  Component,
  ElementRef,
  OnDestroy,
  OnInit,
  ViewChild,
} from '@angular/core';
import {CommonModule} from '@angular/common';
import {FormsModule} from '@angular/forms';
import {MatButtonModule} from '@angular/material/button';
import {MatIconModule} from '@angular/material/icon';
import {EMPTY, Subscription, timer} from 'rxjs';
import {catchError, exhaustMap, switchMap} from 'rxjs/operators';
import {DiagnosemeReworkCard} from '../diagnoseme-rework-card/diagnoseme-rework-card';
import {DiagnosemeReworkCardColumn} from '../diagnoseme-rework-card-column/diagnoseme-rework-card-column';
import {MauiStep3LabelingComponent} from '../maui-step3-labeling/maui-step3-labeling';
import {MauiDeviceMgmtService} from '../services/maui-device-mgmt.service';
import {MauiMcuService} from '../services/maui-mcu.service';
import {TestPassFailButton} from '../test-pass-fail-button/test-pass-fail-button';
import {
  MauiUnitStatusResponse,
  MauiVerifyLabelResponse,
} from '../../proto/maui-manufacturing.pb';

/** Poll period of the enumeration status card. */
export const UNIT_STATUS_POLL_MS = 1000;
/** Stage log written by this page (see V12_STAGE_LOG_MAPPING). */
export const SERIAL_LABEL_LOG = 'serial_label_check';
/** Station id of the final submission ("FATP" selects fatp_tests/). */
export const SERIAL_LABEL_STATION_ID = 'STATION_FATP_V12_SERIAL_BARCODE';

/** Where the scanned text would come from if we accepted it. */
const BLOCKED_INPUT_TYPES = [
  'insertFromPaste',
  'insertFromPasteAsQuotation',
  'insertFromDrop',
  'insertFromYank',
  'insertReplacementText',
];

/** MCU and H2H enumerated with the same, valid serial number. */
function isEnumerationOk(s: MauiUnitStatusResponse | null): boolean {
  return !!(
    s?.mcuDetected &&
    s.h2hDetected &&
    s.h2hSerialValid &&
    s.serialsMatch
  );
}

/**
 * Maui v1.2 enumeration status, serial number and barcode check page.
 *
 * Reuses the v1.1 labeling page (serial readout and on-screen barcode),
 * projects a live enumeration status card above it and a barcode check card
 * below it. The barcode must be typed by a scanner or keyboard (pasting is
 * blocked) and must match both the H2H and the MCU serial numbers. The
 * result is then submitted as a log together with the enumeration info.
 */
@Component({
  selector: 'app-maui-v12-serial',
  standalone: true,
  templateUrl: './maui-v12-serial.html',
  styleUrl: './maui-v12-serial.scss',
  imports: [
    CommonModule,
    DiagnosemeReworkCard,
    DiagnosemeReworkCardColumn,
    FormsModule,
    MatButtonModule,
    MatIconModule,
    MauiStep3LabelingComponent,
    TestPassFailButton,
  ],
})
export class MauiV12SerialComponent implements OnInit, OnDestroy {
  status: MauiUnitStatusResponse | null = null;
  error = '';

  // Barcode check.
  scannedBarcode = '';
  /** Characters typed into the barcode field (paste protection). */
  private typedChars = 0;
  inputWarning = '';
  barcodeChecking = false;
  barcodeResult: MauiVerifyLabelResponse | null = null;
  /** Unit serial the barcode result belongs to. */
  checkedUnitSerial = '';
  /** Status snapshot taken when the barcode was checked (for the log). */
  private checkedStatus: MauiUnitStatusResponse | null = null;

  // Log submission.
  submitting = false;
  submitted = false;
  submitMessage = '';
  submitError = '';

  @ViewChild('barcodeInput') barcodeInput?: ElementRef<HTMLInputElement>;
  @ViewChild('enumStatus') enumStatus?: TestPassFailButton;
  @ViewChild('barcodeStatus') barcodeStatus?: TestPassFailButton;

  /** Unit serial seen by the previous status poll ('' = no unit). */
  private lastSeenSerial = '';

  private pollSub?: Subscription;
  private readonly subs = new Subscription();

  constructor(
    private mcuService: MauiMcuService,
    private deviceMgmt: MauiDeviceMgmtService,
  ) {}

  ngOnInit(): void {
    this.pollSub = timer(0, UNIT_STATUS_POLL_MS)
      .pipe(
        exhaustMap(() =>
          // Errors are handled per poll so that polling survives e.g. a
          // server restart or a replugged board.
          this.mcuService.getUnitStatus().pipe(
            catchError(err => {
              this.error = `Failed to read unit status: ${err?.message ?? err}`;
              return EMPTY;
            }),
          ),
        ),
      )
      .subscribe(status => {
        this.status = status;
        this.error = '';
        this.onUnitChanged();
        this.updateStatusIcons();
        this.focusBarcodeInput();
      });
  }

  ngOnDestroy(): void {
    this.pollSub?.unsubscribe();
    this.subs.unsubscribe();
  }

  yesNo(value: boolean | undefined): string {
    return value ? 'Yes' : 'No';
  }

  /** Console readouts arrive after the USB info (read in the background). */
  consoleText(value: string | undefined): string {
    if (value) {
      return value;
    }
    return this.status?.consolePending ? 'reading...' : '-';
  }

  get hwrevText(): string {
    const rev = this.status?.hwrev ?? -1;
    return rev >= 0 ? `${rev}` : '';
  }

  get h2hSerial(): string {
    return this.status?.h2hSerial ?? '';
  }

  get mcuSerial(): string {
    return this.status?.mcu?.serialNumber ?? '';
  }

  /** Serial number of the connected unit (H2H first, like the backend). */
  get unitSerial(): string {
    return this.h2hSerial || this.mcuSerial;
  }

  /** MCU and H2H enumerated with the same, valid serial number. */
  get enumerationOk(): boolean {
    return isEnumerationOk(this.status);
  }

  get canScan(): boolean {
    return (
      !!this.unitSerial &&
      !this.barcodeChecking &&
      !this.barcodeResult &&
      !this.submitting
    );
  }

  get barcodePassed(): boolean {
    return !!this.barcodeResult?.matches;
  }

  get overallPassed(): boolean {
    return this.enumerationOk && this.barcodePassed;
  }

  // ---- Barcode input: scanner / keyboard only ----

  /** Blocks paste, drop and the context menu on the barcode field. */
  blockInput(event: Event): void {
    event.preventDefault();
    if (event.type !== 'dragover') {
      this.inputWarning =
        'Pasting is disabled. Scan the label barcode (or type the serial).';
    }
  }

  onBeforeInput(event: InputEvent): void {
    if (BLOCKED_INPUT_TYPES.includes(event.inputType)) {
      this.blockInput(event);
    }
  }

  onBarcodeKeydown(event: KeyboardEvent): void {
    const key = event.key ?? '';
    const lower = key.toLowerCase();
    const pasteShortcut =
      ((event.ctrlKey || event.metaKey) && lower === 'v') ||
      (event.shiftKey && key === 'Insert');
    if (pasteShortcut) {
      this.blockInput(event);
      return;
    }
    if (key === 'Enter') {
      event.preventDefault();
      this.verifyBarcode();
      return;
    }
    if (key.length === 1 && !event.ctrlKey && !event.metaKey && !event.altKey) {
      this.typedChars++;
    }
  }

  verifyBarcode(): void {
    const scanned = this.scannedBarcode.trim();
    if (!scanned || !this.canScan) {
      return;
    }
    if (this.typedChars < scanned.length) {
      // Text that did not come from key presses (e.g. a paste path we did
      // not catch) is rejected.
      this.inputWarning =
        'The serial must be scanned or typed, not pasted. Please scan again.';
      this.clearBarcode();
      return;
    }
    this.inputWarning = '';
    this.barcodeChecking = true;
    this.checkedUnitSerial = this.unitSerial;
    this.checkedStatus = this.status;
    this.updateStatusIcons();
    this.subs.add(
      this.deviceMgmt
        .verifyScannedLabel(
          this.unitSerial,
          scanned,
          this.h2hSerial,
          this.mcuSerial,
        )
        .subscribe({
          next: res => {
            this.barcodeChecking = false;
            this.barcodeResult = res;
            if (res.matches) {
              this.submitLogs();
            }
            this.updateStatusIcons();
          },
          error: err => {
            this.barcodeChecking = false;
            this.inputWarning = `Barcode check failed: ${err?.message ?? err}`;
            this.updateStatusIcons();
          },
        }),
    );
  }

  /** Discards the current scan so the operator can scan again. */
  rescan(): void {
    this.resetCheck();
    setTimeout(() => this.barcodeInput?.nativeElement.focus());
  }

  /**
   * Keeps the barcode field focused while a scan is expected, so that the
   * scanner's keystrokes land in it. Focus is not taken from other fields.
   */
  private focusBarcodeInput(): void {
    if (!this.canScan) {
      return;
    }
    setTimeout(() => {
      const input = this.barcodeInput?.nativeElement;
      const active = document.activeElement;
      if (input && (!active || active === document.body)) {
        input.focus();
      }
    });
  }

  private clearBarcode(): void {
    this.scannedBarcode = '';
    this.typedChars = 0;
  }

  private resetCheck(): void {
    this.clearBarcode();
    this.barcodeChecking = false;
    this.barcodeResult = null;
    this.checkedUnitSerial = '';
    this.checkedStatus = null;
    this.submitting = false;
    this.submitted = false;
    this.submitMessage = '';
    this.submitError = '';
    this.updateStatusIcons();
  }

  /**
   * Arms the barcode check for every newly connected unit, starts over when
   * a different board shows up and submits a pending result when the unit
   * is disconnected before its logs were submitted.
   */
  private onUnitChanged(): void {
    const serial = this.unitSerial;
    const previous = this.lastSeenSerial;
    this.lastSeenSerial = serial;
    if (this.submitting || this.barcodeChecking) {
      return;
    }
    if (!serial) {
      if (previous && this.barcodeResult && !this.submitted) {
        this.submitLogs();
      }
      return;
    }
    const newConnection = !previous;
    const otherBoard =
      !!this.checkedUnitSerial && serial !== this.checkedUnitSerial;
    if (newConnection || otherBoard) {
      this.resetCheck();
      this.inputWarning = '';
    }
  }

  /** Mirrors the card verdicts in the pass/fail icons of column 3. */
  updateStatusIcons(): void {
    if (
      !this.status ||
      (!this.status.mcuDetected && !this.status.h2hDetected)
    ) {
      this.enumStatus?.hide();
    } else if (this.enumerationOk) {
      this.enumStatus?.showPassed();
    } else {
      this.enumStatus?.showFailed();
    }

    if (this.barcodeChecking || this.submitting) {
      this.barcodeStatus?.showWorking();
    } else if (!this.barcodeResult) {
      this.barcodeStatus?.hide();
    } else if (this.overallPassed) {
      this.barcodeStatus?.showPassed();
    } else {
      this.barcodeStatus?.showFailed();
    }
  }

  // ---- Log submission ----

  buildLog(): string {
    const s = this.checkedStatus ?? this.status;
    const r = this.barcodeResult;
    const passed = isEnumerationOk(s) && this.barcodePassed;
    const hwrev = s && s.hwrev >= 0 ? `${s.hwrev}` : '-';
    return (
      '=== Maui v1.2 Enumeration, Serial & Barcode Check ===\n' +
      `Date: ${new Date().toISOString()}\n` +
      `ALL_PASSED=${passed}\n` +
      `RESULT: ${passed ? 'PASSED' : 'FAILED'}\n` +
      `Unit Serial: ${this.checkedUnitSerial || '-'}\n` +
      '\n--- Enumeration ---\n' +
      `MCU console detected: ${this.yesNo(s?.mcuDetected)}` +
      ` (port ${s?.mcu?.devicePort || '-'}, stage ${s?.mcu?.stage || '-'})\n` +
      `MCU USB iSerial: ${s?.mcu?.serialNumber || '-'}\n` +
      `MCU NVS serial: ${s?.mcuNvsSerial || '-'}\n` +
      `MCU version: ${s?.mcuVersion || '-'}\n` +
      `HW rev: ${hwrev}\n` +
      `H2H detected: ${this.yesNo(s?.h2hDetected)}\n` +
      `H2H serial: ${s?.h2hSerial || '-'}` +
      ` (valid: ${this.yesNo(s?.h2hSerialValid)})\n` +
      `MCU and H2H serials match: ${this.yesNo(s?.serialsMatch)}\n` +
      `USB 2.0 hub: ${this.yesNo(s?.hubUsb2Detected)}\n` +
      `USB 3.x hub: ${this.yesNo(s?.hubUsb3Detected)}\n` +
      `Status: ${s?.message || '-'}\n` +
      '\n--- Barcode ---\n' +
      `Scanned barcode: ${r?.scannedBarcode || this.scannedBarcode.trim()}\n` +
      `Barcode matches H2H and MCU: ${r?.matches ? 'PASS' : 'FAIL'}\n` +
      `Message: ${r?.message || '-'}\n`
    );
  }

  /** Saves the page log and submits the final result package. */
  submitLogs(): void {
    if (!this.barcodeResult || this.submitting || this.submitted) {
      return;
    }
    const serial = this.checkedUnitSerial || this.unitSerial || 'UNKNOWN';
    const s = this.checkedStatus ?? this.status;
    const scanned = this.barcodeResult.scannedBarcode || this.scannedBarcode;
    this.submitting = true;
    this.submitError = '';
    this.subs.add(
      this.deviceMgmt
        .saveCardLog(serial, SERIAL_LABEL_LOG, this.buildLog())
        .pipe(
          switchMap(() =>
            this.deviceMgmt.submitFinalManufacturingResults(
              serial,
              'OP_FACTORY',
              SERIAL_LABEL_STATION_ID,
              false,
              false,
              false,
              false,
              false,
              this.barcodePassed,
              undefined,
              s?.h2hSerial ?? '',
              s?.mcu?.serialNumber ?? '',
              scanned,
            ),
          ),
        )
        .subscribe({
          next: res => {
            this.submitting = false;
            this.submitted = true;
            this.submitMessage = res.message || 'Results submitted.';
            this.updateStatusIcons();
          },
          error: err => {
            this.submitting = false;
            this.submitError = `Failed to submit logs: ${err?.message ?? err}`;
            this.updateStatusIcons();
          },
        }),
    );
  }
}
