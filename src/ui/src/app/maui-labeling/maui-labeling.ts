// Copyright 2026 The ChromiumOS Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

import {
  ChangeDetectorRef,
  Component,
  ElementRef,
  HostListener,
  OnDestroy,
  OnInit,
  ViewChild,
  inject,
} from '@angular/core';
import {CommonModule} from '@angular/common';
import {FormsModule} from '@angular/forms';
import {MatButtonModule} from '@angular/material/button';
import {MatFormFieldModule} from '@angular/material/form-field';
import {MatIconModule} from '@angular/material/icon';
import {MatInputModule} from '@angular/material/input';
import {Subscription} from 'rxjs';
import {DiagnosemeReworkCard} from '../diagnoseme-rework-card/diagnoseme-rework-card';
import {DiagnosemeReworkCardColumn} from '../diagnoseme-rework-card-column/diagnoseme-rework-card-column';
import {MauiDialogService} from '../maui-common/maui-dialog.service';
import {MauiStationFooterComponent} from '../maui-common/maui-station-footer/maui-station-footer';
import {autoAdvanceToCard} from '../maui-common/maui-scroll.utils';
import {MauiDeviceMgmtService} from '../services/maui-device-mgmt.service';
import {MauiLogFileInfo} from '../../proto/maui-manufacturing.pb';
import {TestPassFailButton} from '../test-pass-fail-button/test-pass-fail-button';

@Component({
  selector: 'app-maui-labeling',
  standalone: true,
  templateUrl: './maui-labeling.html',
  styleUrl: './maui-labeling.scss',
  imports: [
    CommonModule,
    DiagnosemeReworkCard,
    DiagnosemeReworkCardColumn,
    FormsModule,
    MatButtonModule,
    MatFormFieldModule,
    MatIconModule,
    MatInputModule,
    MauiStationFooterComponent,
    TestPassFailButton,
  ],
})
export class MauiLabelingComponent implements OnInit, OnDestroy {
  private readonly deviceMgmtService = inject(MauiDeviceMgmtService);
  private readonly dialogService = inject(MauiDialogService);
  private readonly cdr = inject(ChangeDetectorRef);

  private readonly subs = new Subscription();

  electronicSerial = '';
  adbSerial = '';
  adbConnected = false;
  adbProduct = '';
  ftdiSerial = '';
  ftdiConnected = false;
  ftdiProduct = '';
  dualEndpointsMatch = false;
  deviceConnected = false;
  readingSerial = false;
  serialDetectionMessage = 'Detecting connected Maui device...';
  operatorId = 'OP_FACTORY_01';
  stationId = 'STATION_FINAL_01';

  // Barcode scanner global input buffer
  private barcodeBuffer = '';
  private lastBarcodeKeyTime = 0;

  // Card 1: Print Mockup
  labelPrinted = false;
  printStatusMessage = '';

  // Card 2: Barcode Verification
  scannedBarcode = '';
  barcodeMatches = false;
  barcodeCheckInProgress = false;
  barcodeCheckFailed = false;
  barcodeMessage = '';
  adbMatches = false;
  ftdiMatches = false;

  // Card 3: Log Directory Inspection
  logCheckInProgress = false;
  logDirectoryPath = '';
  availableLogs: MauiLogFileInfo[] = [];
  allLogsPresent = false;
  logStatusMessage = '';
  overallLogsVerdict = '';
  stageStatuses: Record<string, string> = {};

  // Card 4: Final Submission
  submissionInProgress = false;
  submissionComplete = false;
  mesTrackingId = '';
  summaryFilePath = '';
  finalOverallStatus = '';

  @ViewChild('printStatus') printStatus!: TestPassFailButton;
  @ViewChild('scanStatus') scanStatus!: TestPassFailButton;
  @ViewChild('logStatus') logStatus!: TestPassFailButton;
  @ViewChild('submitStatus') submitStatus!: TestPassFailButton;

  @ViewChild('printBtn', {read: ElementRef})
  printBtn?: ElementRef<HTMLButtonElement>;
  @ViewChild('scanInput', {read: ElementRef})
  scanInput?: ElementRef<HTMLInputElement>;
  @ViewChild('verifyBarcodeBtn', {read: ElementRef})
  verifyBarcodeBtn?: ElementRef<HTMLButtonElement>;
  @ViewChild('inspectLogsBtn', {read: ElementRef})
  inspectLogsBtn?: ElementRef<HTMLButtonElement>;
  @ViewChild('submitMesBtn', {read: ElementRef})
  submitMesBtn?: ElementRef<HTMLButtonElement>;

  ngOnInit(): void {
    this.readElectronicSerial();
  }

  ngOnDestroy(): void {
    this.subs.unsubscribe();
  }

  // Barcode Wedge / Global Keystroke Interceptor
  @HostListener('window:keydown', ['$event'])
  handleKeyboardEvent(event: KeyboardEvent): void {
    const activeEl = document.activeElement as HTMLElement;
    if (
      activeEl &&
      (activeEl.tagName === 'INPUT' || activeEl.tagName === 'TEXTAREA') &&
      activeEl !== this.scanInput?.nativeElement
    ) {
      return;
    }

    const now = Date.now();
    if (now - this.lastBarcodeKeyTime > 150) {
      this.barcodeBuffer = '';
    }
    this.lastBarcodeKeyTime = now;

    if (event.key === 'Enter') {
      if (this.barcodeBuffer.length >= 6) {
        event.preventDefault();
        this.scannedBarcode = this.barcodeBuffer.trim().toUpperCase();
        this.barcodeBuffer = '';
        this.verifyBarcode();
      }
    } else if (event.key.length === 1) {
      this.barcodeBuffer += event.key;
    }
  }

  // 1. Dual Endpoint Serial Readout & Hardware Validation
  readElectronicSerial(): void {
    this.readingSerial = true;
    this.serialDetectionMessage = 'Reading connected device USB endpoints...';
    this.cdr.detectChanges();

    this.subs.add(
      this.deviceMgmtService.readConnectedDeviceSerial().subscribe({
        next: res => {
          this.readingSerial = false;
          this.adbSerial = res.adbSerial || '';
          this.adbConnected = res.adbConnected;
          this.adbProduct = res.adbProduct || '';
          this.ftdiSerial = res.ftdiSerial || '';
          this.ftdiConnected = res.ftdiConnected;
          this.ftdiProduct = res.ftdiProduct || '';
          this.dualEndpointsMatch = res.dualEndpointsMatch;
          this.deviceConnected = res.isConnected;
          this.electronicSerial = res.serialNumber || '';

          if (this.dualEndpointsMatch && res.serialNumber) {
            this.serialDetectionMessage =
              'Hardware Connected (Dual Endpoints Matched)';
            this.inspectLogs();
          } else if (!res.adbConnected && !res.ftdiConnected) {
            this.electronicSerial = '';
            this.serialDetectionMessage = 'No Device Detected';
          } else if (!res.adbConnected || !res.ftdiConnected) {
            const missing = !res.adbConnected
              ? 'ADB (18d1:506d)'
              : 'FTDI (0403:6002)';
            this.serialDetectionMessage = `Endpoint Missing: ${missing}`;
            if (this.electronicSerial) {
              this.inspectLogs();
            }
          } else {
            this.serialDetectionMessage = `Serial Mismatch (ADB: ${this.adbSerial} vs FTDI: ${this.ftdiSerial})`;
          }
          this.cdr.detectChanges();
        },
        error: err => {
          this.readingSerial = false;
          this.deviceConnected = false;
          this.electronicSerial = '';
          this.serialDetectionMessage =
            'Detection Error: ' +
            (err.message || 'Unable to scan USB topology.');
          this.cdr.detectChanges();
        },
      }),
    );
  }

  // 1. Print Mockup
  printLabel(): void {
    if (!this.electronicSerial) {
      return;
    }
    this.labelPrinted = true;
    this.printStatusMessage = `Barcode label sent to factory thermal printer (${this.electronicSerial})`;
    this.printStatus?.showPassed();
    autoAdvanceToCard('label-card-2', this.scanInput);
    this.cdr.detectChanges();
  }

  triggerPrintLabel(): void {
    this.printLabel();
  }

  bypassLabelPrint(): void {
    this.labelPrinted = true;
    this.printStatusMessage = 'Label printing bypassed by operator.';
    this.printStatus?.showBypassed();
    autoAdvanceToCard('label-card-2', this.scanInput);
    this.cdr.detectChanges();
  }

  // 2. Barcode Label Verification
  verifyBarcode(): void {
    const cleanBarcode = (this.scannedBarcode || '').trim().toUpperCase();
    if (!cleanBarcode) {
      return;
    }

    this.barcodeCheckInProgress = true;
    this.scanStatus?.showWorking();
    this.cdr.detectChanges();

    this.subs.add(
      this.deviceMgmtService
        .verifyScannedLabel(
          this.electronicSerial,
          cleanBarcode,
          this.adbSerial,
          this.ftdiSerial,
        )
        .subscribe({
          next: res => {
            this.barcodeCheckInProgress = false;
            this.barcodeMatches = res.matches;
            this.barcodeMessage = res.message;
            this.adbMatches = res.adbMatches;
            this.ftdiMatches = res.ftdiMatches;

            if (res.matches) {
              this.barcodeCheckFailed = false;
              this.scanStatus?.showPassed();
              autoAdvanceToCard('label-card-4', this.submitMesBtn);
            } else {
              this.barcodeCheckFailed = true;
              this.scanStatus?.showFailed();
              this.dialogService.openFailure(
                'Barcode Label Mismatch',
                res.message,
                () => this.verifyBarcode(),
                () => this.bypassBarcode(),
              );
            }
            this.cdr.detectChanges();
          },
          error: err => {
            this.barcodeCheckInProgress = false;
            this.barcodeCheckFailed = true;
            this.scanStatus?.showFailed();
            this.dialogService.openFailure(
              'Barcode Verification Error',
              err,
              () => this.verifyBarcode(),
              () => this.bypassBarcode(),
            );
            this.cdr.detectChanges();
          },
        }),
    );
  }

  bypassBarcode(): void {
    this.barcodeMatches = true;
    if (this.barcodeCheckFailed) {
      this.scanStatus?.showFailed();
      this.barcodeMessage =
        'Barcode verification failed (Bypassed by operator).';
    } else {
      this.barcodeMessage = 'Barcode verification bypassed by operator.';
      this.scanStatus?.showBypassed();
    }
    autoAdvanceToCard('label-card-4', this.submitMesBtn);
    this.cdr.detectChanges();
  }

  // 3. Inspect Log Directory (/tmp/<serial>_diagnoseme_logs/)
  inspectLogs(): void {
    if (!this.electronicSerial) {
      return;
    }
    this.logCheckInProgress = true;
    this.logStatus?.showWorking();
    this.cdr.detectChanges();

    this.subs.add(
      this.deviceMgmtService
        .getDeviceLogsStatus(this.electronicSerial)
        .subscribe({
          next: res => {
            this.logCheckInProgress = false;
            this.logDirectoryPath = res.directoryPath;
            this.availableLogs = res.logs || [];
            this.allLogsPresent = res.allRequiredLogsPresent;
            this.logStatusMessage = res.message;
            this.overallLogsVerdict = res.overallVerdict;
            this.stageStatuses = res.stageStatuses || {};

            if (res.allRequiredLogsPresent && res.overallVerdict !== 'FAIL') {
              this.logStatus?.showPassed();
              autoAdvanceToCard('label-card-4', this.submitMesBtn);
            } else if (res.overallVerdict === 'FAIL') {
              this.logStatus?.showFailed();
            } else {
              this.logStatus?.showWorking();
            }
            this.cdr.detectChanges();
          },
          error: err => {
            this.logCheckInProgress = false;
            this.logStatus?.showFailed();
            this.dialogService.openFailure(
              'Log Inspection Error',
              err,
              () => this.inspectLogs(),
              () => this.bypassLogs(),
            );
            this.cdr.detectChanges();
          },
        }),
    );
  }

  bypassLogs(): void {
    this.allLogsPresent = true;
    if (this.overallLogsVerdict === 'FAIL') {
      this.logStatus?.showFailed();
      this.logStatusMessage = 'Log verification failed (Bypassed by operator).';
    } else {
      this.logStatusMessage = 'Log directory inspection bypassed by operator.';
      this.logStatus?.showBypassed();
    }
    autoAdvanceToCard('label-card-4', this.submitMesBtn);
    this.cdr.detectChanges();
  }

  // 4. Submit Final Manufacturing Results to MES & Write final_results.json
  submitFinalResults(): void {
    if (!this.electronicSerial) {
      this.electronicSerial =
        this.scannedBarcode.trim() || 'MANUAL_SERIAL_' + Date.now();
    }

    this.submissionInProgress = true;
    this.submitStatus?.showWorking();
    this.cdr.detectChanges();

    this.subs.add(
      this.deviceMgmtService
        .submitFinalManufacturingResults(
          this.electronicSerial,
          this.operatorId,
          this.stationId,
          true,
          true,
          true,
          true,
          true,
          this.barcodeMatches,
          this.stageStatuses,
          this.adbSerial,
          this.ftdiSerial,
          this.scannedBarcode.trim(),
        )
        .subscribe({
          next: res => {
            this.submissionInProgress = false;
            this.submissionComplete = res.success;
            this.mesTrackingId = res.mesTrackingId;
            this.summaryFilePath = res.summaryFilePath;
            this.finalOverallStatus = res.message;

            if (res.success) {
              this.submitStatus?.showPassed();
            } else {
              this.submitStatus?.showFailed();
              this.dialogService.openFailure(
                'Final Manufacturing Submission Failed',
                res.message,
                () => this.submitFinalResults(),
                () => {
                  this.submissionComplete = true;
                  this.mesTrackingId = 'MES-FAILED-BYPASS-' + Date.now();
                  this.submitStatus?.showFailed();
                },
              );
            }
            this.cdr.detectChanges();
          },
          error: err => {
            this.submissionInProgress = false;
            this.submitStatus?.showFailed();
            this.dialogService.openFailure(
              'Submission Error',
              err,
              () => this.submitFinalResults(),
              () => {
                this.submissionComplete = true;
                this.mesTrackingId = 'MES-FAILED-BYPASS-' + Date.now();
                this.submitStatus?.showFailed();
              },
            );
            this.cdr.detectChanges();
          },
        }),
    );
  }

  saveLogs(): void {
    this.submitFinalResults();
  }
}
