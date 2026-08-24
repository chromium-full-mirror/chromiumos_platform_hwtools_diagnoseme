// Copyright 2026 The ChromiumOS Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

import {
  ChangeDetectorRef,
  Component,
  ElementRef,
  OnDestroy,
  OnInit,
  ViewChild,
  inject,
} from '@angular/core';
import {CommonModule} from '@angular/common';
import {FormsModule} from '@angular/forms';
import {MatButtonModule} from '@angular/material/button';
import {MatCheckboxModule} from '@angular/material/checkbox';
import {MatIconModule} from '@angular/material/icon';
import {Subscription} from 'rxjs';
import {DiagnosemeReworkCard} from '../diagnoseme-rework-card/diagnoseme-rework-card';
import {DiagnosemeReworkCardColumn} from '../diagnoseme-rework-card-column/diagnoseme-rework-card-column';
import {MauiDialogService} from '../maui-common/maui-dialog.service';
import {MauiStationFooterComponent} from '../maui-common/maui-station-footer/maui-station-footer';
import {autoAdvanceToCard} from '../maui-common/maui-scroll.utils';
import {MauiStation2Service} from '../services/maui-station2.service';
import {MauiDeviceMgmtService} from '../services/maui-device-mgmt.service';
import {TestPassFailButton} from '../test-pass-fail-button/test-pass-fail-button';

@Component({
  selector: 'app-maui-host-testing',
  standalone: true,
  templateUrl: './maui-host-testing.html',
  styleUrl: './maui-host-testing.scss',
  imports: [
    CommonModule,
    DiagnosemeReworkCard,
    DiagnosemeReworkCardColumn,
    FormsModule,
    MatButtonModule,
    MatCheckboxModule,
    MatIconModule,
    MauiStationFooterComponent,
    TestPassFailButton,
  ],
})
export class MauiHostTestingComponent implements OnInit, OnDestroy {
  private readonly mauiService = inject(MauiStation2Service);
  private readonly deviceMgmtService = inject(MauiDeviceMgmtService);
  private readonly dialogService = inject(MauiDialogService);
  private readonly cdr = inject(ChangeDetectorRef);

  private readonly subs = new Subscription();

  serialNumber = '';
  devicePort = '';
  deviceConnected = false;
  deviceProgrammed = false;
  ftdiPid = '';
  statusMessage = 'Scanning USB bus for connected Maui device...';

  // Section 1: USB & Console Check
  usbCheckInProgress = false;
  usbCheckPassed = false;
  usbStatusValue: 'NOT_RUN' | 'PASSED' | 'FAILED' | 'BYPASSED' = 'NOT_RUN';
  usbSummary = '';
  mcuVersionStr = '';
  pdcAppConfigStr = '';

  // Section 2: Host Cable 180° Flip Test
  replugInProgress = false;
  replugPassed = false;
  replugStatusValue: 'NOT_RUN' | 'PASSED' | 'FAILED' | 'BYPASSED' = 'NOT_RUN';
  replugMessage = '';

  // Section 3: RESET Button Test
  resetBaselineInProgress = false;
  resetBaselineCaptured = false;
  initialUptimeMs = 0;
  resetVerifyInProgress = false;
  resetPassed = false;
  resetStatusValue: 'NOT_RUN' | 'PASSED' | 'FAILED' | 'BYPASSED' = 'NOT_RUN';
  resetMessage = '';

  // Section 4: BSL Button Test (ADC Status)
  bslAdcInProgress = false;
  bslPassed = false;
  bslStatusValue: 'NOT_RUN' | 'PASSED' | 'FAILED' | 'BYPASSED' = 'NOT_RUN';
  peakBslMv = 0;
  bslMessage = '';

  // Section 5: Visual LED Check
  led1Green1hz = false;
  led2AmberSolid = false;
  ledPassed = false;
  ledStatusValue: 'NOT_RUN' | 'PASSED' | 'FAILED' | 'BYPASSED' = 'NOT_RUN';

  // Section 6: Log Saving
  savingLogs = false;
  logsSaved = false;

  @ViewChild('usbStatus') usbStatus!: TestPassFailButton;
  @ViewChild('replugStatus') replugStatus!: TestPassFailButton;
  @ViewChild('resetStatus') resetStatus!: TestPassFailButton;
  @ViewChild('bslStatus') bslStatus!: TestPassFailButton;
  @ViewChild('ledStatus') ledStatus!: TestPassFailButton;
  @ViewChild('completeStatus') completeStatus!: TestPassFailButton;

  @ViewChild('usbBtn', {read: ElementRef})
  usbBtn?: ElementRef<HTMLButtonElement>;
  @ViewChild('flipBtn', {read: ElementRef})
  flipBtn?: ElementRef<HTMLButtonElement>;
  @ViewChild('resetBaselineBtn', {read: ElementRef})
  resetBaselineBtn?: ElementRef<HTMLButtonElement>;
  @ViewChild('resetVerifyBtn', {read: ElementRef})
  resetVerifyBtn?: ElementRef<HTMLButtonElement>;
  @ViewChild('bslBtn', {read: ElementRef})
  bslBtn?: ElementRef<HTMLButtonElement>;
  @ViewChild('ledConfirmBtn', {read: ElementRef})
  ledConfirmBtn?: ElementRef<HTMLButtonElement>;
  @ViewChild('saveLogsBtn', {read: ElementRef})
  saveLogsBtn?: ElementRef<HTMLButtonElement>;

  ngOnInit(): void {
    this.refreshDeviceStatus();
  }

  refreshDeviceStatus(): void {
    this.subs.add(
      this.deviceMgmtService.readConnectedDeviceSerial().subscribe({
        next: res => {
          this.deviceConnected = res.isConnected;
          this.deviceProgrammed = !!res.ftdiIsProgrammed;
          this.ftdiPid = res.ftdiPid ? '0x' + res.ftdiPid.toString(16) : '';
          this.statusMessage = res.message || '';
          if (res.isConnected && res.serialNumber) {
            this.serialNumber = res.serialNumber;
            this.devicePort = res.devicePort || '/dev/ttyUSB0';
          }
          this.cdr.detectChanges();
        },
        error: () => {},
      }),
    );
  }

  ngOnDestroy(): void {
    this.subs.unsubscribe();
  }

  // 1. Host USB Enumeration, PDC status & Quick Test
  runUsbCheck(): void {
    this.usbCheckInProgress = true;
    this.usbStatus?.showWorking();
    this.cdr.detectChanges();

    this.subs.add(
      this.mauiService
        .runHostOnlyTests(this.serialNumber, this.devicePort)
        .subscribe({
          next: res => {
            this.usbCheckInProgress = false;
            this.usbCheckPassed = res.allPassed;
            this.usbSummary = res.summary;
            this.mcuVersionStr = res.mcuVersionStr;
            this.pdcAppConfigStr = res.pdcAppconfigVer;

            if (res.allPassed) {
              this.usbStatusValue = 'PASSED';
              this.usbStatus?.showPassed();
              autoAdvanceToCard('host-card-2', this.flipBtn);
            } else {
              this.usbStatusValue = 'FAILED';
              this.usbStatus?.showFailed();
              this.dialogService.openFailure(
                'Host USB & Console Check Failed',
                res.summary ||
                  'One or more USB / Console devices failed enumeration.',
                () => this.runUsbCheck(),
                () => this.bypassUsbCheck(),
              );
            }
            this.cdr.detectChanges();
          },
          error: err => {
            this.usbCheckInProgress = false;
            this.usbStatusValue = 'FAILED';
            this.usbStatus?.showFailed();
            this.dialogService.openFailure(
              'Host USB Check Error',
              err,
              () => this.runUsbCheck(),
              () => this.bypassUsbCheck(),
            );
            this.cdr.detectChanges();
          },
        }),
    );
  }

  // 2. Host Cable 180° Flip Test
  runHostReplug(): void {
    this.replugInProgress = true;
    this.replugStatus?.showWorking();
    this.cdr.detectChanges();

    this.subs.add(
      this.mauiService
        .testHostReplug(this.serialNumber, this.devicePort)
        .subscribe({
          next: res => {
            this.replugInProgress = false;
            this.replugPassed = res.allPassed;
            this.replugMessage = res.message;

            if (res.allPassed) {
              this.replugStatusValue = 'PASSED';
              this.replugStatus?.showPassed();
              autoAdvanceToCard('host-card-3', this.resetBaselineBtn);
            } else {
              this.replugStatusValue = 'FAILED';
              this.replugStatus?.showFailed();
              this.dialogService.openFailure(
                'Host Re-plug Test Failed',
                res.message,
                () => this.runHostReplug(),
                () => this.bypassHostReplug(),
              );
            }
            this.cdr.detectChanges();
          },
          error: err => {
            this.replugInProgress = false;
            this.replugStatusValue = 'FAILED';
            this.replugStatus?.showFailed();
            this.dialogService.openFailure(
              'Host Re-plug Error',
              err,
              () => this.runHostReplug(),
              () => this.bypassHostReplug(),
            );
            this.cdr.detectChanges();
          },
        }),
    );
  }

  // 3. RESET Button - Step A: Baseline Uptime Check (>= 10s)
  checkResetBaseline(): void {
    this.resetBaselineInProgress = true;
    this.resetStatus?.showWorking();
    this.cdr.detectChanges();

    this.subs.add(
      this.mauiService
        .testResetButton(
          this.serialNumber,
          this.devicePort,
          'check_initial_uptime',
          0,
        )
        .subscribe({
          next: res => {
            this.resetBaselineInProgress = false;
            if (res.success && res.uptimeSufficient) {
              this.resetBaselineCaptured = true;
              this.initialUptimeMs = Number(res.currentUptimeMs);
              this.resetMessage = `Baseline uptime: ${this.initialUptimeMs} ms (>= 10s). Operator: Press RESET button now!`;
              setTimeout(
                () => this.resetVerifyBtn?.nativeElement?.focus(),
                400,
              );
            } else {
              this.resetStatus?.showFailed();
              this.dialogService.openFailure(
                'RESET Baseline Check Failed',
                res.message ||
                  `Current uptime was only ${res.currentUptimeMs} ms (< 10s required).`,
                () => this.checkResetBaseline(),
                () => {
                  this.resetBaselineCaptured = true;
                  this.initialUptimeMs = 15000;
                  this.resetMessage =
                    'Baseline uptime check bypassed by operator. Press RESET button now.';
                  setTimeout(
                    () => this.resetVerifyBtn?.nativeElement?.focus(),
                    400,
                  );
                },
              );
            }
            this.cdr.detectChanges();
          },
          error: err => {
            this.resetBaselineInProgress = false;
            this.resetStatus?.showFailed();
            this.dialogService.openFailure(
              'RESET Baseline Error',
              err,
              () => this.checkResetBaseline(),
              () => {
                this.resetBaselineCaptured = true;
                this.initialUptimeMs = 15000;
                this.resetMessage =
                  'Baseline uptime check bypassed by operator. Press RESET button now.';
                setTimeout(
                  () => this.resetVerifyBtn?.nativeElement?.focus(),
                  400,
                );
              },
            );
            this.cdr.detectChanges();
          },
        }),
    );
  }

  // 3. RESET Button - Step B: Verify Post-Reset Reboot (post < baseline)
  verifyResetReboot(): void {
    this.resetVerifyInProgress = true;
    this.resetStatus?.showWorking();
    this.cdr.detectChanges();

    this.subs.add(
      this.mauiService
        .testResetButton(
          this.serialNumber,
          this.devicePort,
          'verify_post_reset_uptime',
          this.initialUptimeMs,
        )
        .subscribe({
          next: res => {
            this.resetVerifyInProgress = false;
            this.resetPassed = res.resetVerified;
            this.resetMessage = res.message;

            if (res.resetVerified) {
              this.resetStatusValue = 'PASSED';
              this.resetStatus?.showPassed();
              autoAdvanceToCard('host-card-4', this.bslBtn);
            } else {
              this.resetStatusValue = 'FAILED';
              this.resetStatus?.showFailed();
              this.dialogService.openFailure(
                'RESET Reboot Verification Failed',
                res.message ||
                  `Post-reset uptime was ${res.currentUptimeMs} ms.`,
                () => this.verifyResetReboot(),
                () => this.bypassResetTest(),
              );
            }
            this.cdr.detectChanges();
          },
          error: err => {
            this.resetVerifyInProgress = false;
            this.resetStatusValue = 'FAILED';
            this.resetStatus?.showFailed();
            this.dialogService.openFailure(
              'RESET Verification Error',
              err,
              () => this.verifyResetReboot(),
              () => this.bypassResetTest(),
            );
            this.cdr.detectChanges();
          },
        }),
    );
  }

  // 4. BSL Button Test (Sample ADC >= 2500mV)
  runBslAdcTest(): void {
    this.bslAdcInProgress = true;
    this.bslStatus?.showWorking();
    this.cdr.detectChanges();

    this.subs.add(
      this.mauiService
        .testBslButton(this.serialNumber, this.devicePort, 5)
        .subscribe({
          next: res => {
            this.bslAdcInProgress = false;
            this.bslPassed = res.success;
            this.peakBslMv = res.peakBslMv;
            this.bslMessage = res.message;

            if (res.success) {
              this.bslStatusValue = 'PASSED';
              this.bslStatus?.showPassed();
              autoAdvanceToCard('host-card-5', this.ledConfirmBtn);
            } else {
              this.bslStatusValue = 'FAILED';
              this.bslStatus?.showFailed();
              this.dialogService.openFailure(
                'BSL Button ADC Test Failed',
                `${res.message}\nPeak ADC: ${res.peakBslMv} mV (Threshold >= 2500 mV)\n\n${res.rawAdcTable}`,
                () => this.runBslAdcTest(),
                () => this.bypassBslTest(),
              );
            }
            this.cdr.detectChanges();
          },
          error: err => {
            this.bslAdcInProgress = false;
            this.bslStatusValue = 'FAILED';
            this.bslStatus?.showFailed();
            this.dialogService.openFailure(
              'BSL Button ADC Error',
              err,
              () => this.runBslAdcTest(),
              () => this.bypassBslTest(),
            );
            this.cdr.detectChanges();
          },
        }),
    );
  }

  // 5. Visual LED Confirmation
  confirmLeds(): void {
    if (this.led1Green1hz && this.led2AmberSolid) {
      this.ledPassed = true;
      this.ledStatusValue = 'PASSED';
      this.ledStatus?.showPassed();
      autoAdvanceToCard('host-card-6', this.saveLogsBtn);
      setTimeout(() => this.saveHostLogs(), 700);
    } else {
      this.ledPassed = false;
      this.ledStatusValue = 'FAILED';
      this.ledStatus?.showFailed();
      this.dialogService.openFailure(
        'Visual LED Verification Incomplete',
        'Both LED checkboxes (Green 1Hz & Amber solid) must be verified and checked.',
        () => this.confirmLeds(),
        () => this.bypassLeds(),
      );
    }
    this.cdr.detectChanges();
  }

  bypassUsbCheck(): void {
    this.usbCheckPassed = true;
    if (this.usbStatusValue === 'FAILED') {
      this.usbStatus?.showFailed();
    } else {
      this.usbStatusValue = 'BYPASSED';
      this.usbStatus?.showBypassed();
    }
    autoAdvanceToCard('host-card-2', this.flipBtn);
    this.cdr.detectChanges();
  }

  bypassHostReplug(): void {
    this.replugPassed = true;
    if (this.replugStatusValue === 'FAILED') {
      this.replugStatus?.showFailed();
    } else {
      this.replugStatusValue = 'BYPASSED';
      this.replugStatus?.showBypassed();
    }
    autoAdvanceToCard('host-card-3', this.resetBaselineBtn);
    this.cdr.detectChanges();
  }

  bypassResetTest(): void {
    this.resetBaselineCaptured = true;
    this.resetPassed = true;
    if (this.resetStatusValue === 'FAILED') {
      this.resetStatus?.showFailed();
    } else {
      this.resetStatusValue = 'BYPASSED';
      this.resetStatus?.showBypassed();
    }
    autoAdvanceToCard('host-card-4', this.bslBtn);
    this.cdr.detectChanges();
  }

  bypassBslTest(): void {
    this.bslPassed = true;
    if (this.bslStatusValue === 'FAILED') {
      this.bslStatus?.showFailed();
    } else {
      this.bslStatusValue = 'BYPASSED';
      this.bslStatus?.showBypassed();
    }
    autoAdvanceToCard('host-card-5', this.ledConfirmBtn);
    this.cdr.detectChanges();
  }

  bypassLeds(): void {
    this.ledPassed = true;
    if (this.ledStatusValue === 'FAILED') {
      this.ledStatus?.showFailed();
    } else {
      this.ledStatusValue = 'BYPASSED';
      this.ledStatus?.showBypassed();
    }
    autoAdvanceToCard('host-card-6', this.saveLogsBtn);
    this.cdr.detectChanges();
  }

  // 6. Save Host Test Logs & Complete
  saveHostLogs(): void {
    this.savingLogs = true;
    this.completeStatus?.showWorking();
    this.cdr.detectChanges();

    const allPassed =
      this.usbStatusValue === 'PASSED' &&
      this.replugStatusValue === 'PASSED' &&
      this.resetStatusValue === 'PASSED' &&
      this.bslStatusValue === 'PASSED' &&
      this.ledStatusValue === 'PASSED';
    const overallVerdictTag = allPassed
      ? 'PASSED'
      : this.usbStatusValue === 'FAILED' ||
          this.replugStatusValue === 'FAILED' ||
          this.resetStatusValue === 'FAILED' ||
          this.bslStatusValue === 'FAILED' ||
          this.ledStatusValue === 'FAILED'
        ? 'FAILED'
        : 'BYPASSED';

    const logSummary = [
      `OVERALL_HOST_STATUS: ${overallVerdictTag}`,
      `ALL_PASSED=${allPassed}`,
      `USB & PDC Quick Test: ${this.usbStatusValue}`,
      `Host Cable 180° Flip: ${this.replugStatusValue}`,
      `RESET Button Reboot: ${this.resetStatusValue}`,
      `BSL Button ADC: ${this.bslStatusValue} (Peak: ${this.peakBslMv} mV)`,
      `Visual LEDs: ${this.ledStatusValue} (Green 1Hz & Amber solid)`,
    ].join('\n');

    this.subs.add(
      this.deviceMgmtService
        .saveCardLog(
          this.serialNumber || 'UNKNOWN_DEVICE',
          'host_only_tests',
          logSummary,
        )
        .subscribe({
          next: () => {
            this.savingLogs = false;
            this.logsSaved = true;
            this.completeStatus?.showPassed();
            this.cdr.detectChanges();
          },
          error: err => {
            this.savingLogs = false;
            this.completeStatus?.showFailed();
            this.dialogService.openFailure(
              'Failed to save Host logs',
              err,
              () => this.saveHostLogs(),
              () => {
                this.logsSaved = true;
                this.completeStatus?.showBypassed();
              },
            );
            this.cdr.detectChanges();
          },
        }),
    );
  }

  saveLogs(): void {
    this.saveHostLogs();
  }
}
