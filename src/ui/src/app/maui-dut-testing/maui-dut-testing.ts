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
import {MatButtonModule} from '@angular/material/button';
import {MatIconModule} from '@angular/material/icon';
import {Subscription, switchMap, timer} from 'rxjs';
import {DiagnosemeReworkCard} from '../diagnoseme-rework-card/diagnoseme-rework-card';
import {DiagnosemeReworkCardColumn} from '../diagnoseme-rework-card-column/diagnoseme-rework-card-column';
import {MauiDialogService} from '../maui-common/maui-dialog.service';
import {MauiStationFooterComponent} from '../maui-common/maui-station-footer/maui-station-footer';
import {autoAdvanceToCard} from '../maui-common/maui-scroll.utils';
import {MauiStation2Service} from '../services/maui-station2.service';
import {MauiDeviceMgmtService} from '../services/maui-device-mgmt.service';
import {TestPassFailButton} from '../test-pass-fail-button/test-pass-fail-button';

@Component({
  selector: 'app-maui-dut-testing',
  standalone: true,
  templateUrl: './maui-dut-testing.html',
  styleUrl: './maui-dut-testing.scss',
  imports: [
    CommonModule,
    DiagnosemeReworkCard,
    DiagnosemeReworkCardColumn,
    MatButtonModule,
    MatIconModule,
    MauiStationFooterComponent,
    TestPassFailButton,
  ],
})
export class MauiDutTestingComponent implements OnInit, OnDestroy {
  private readonly mauiService = inject(MauiStation2Service);
  private readonly deviceMgmtService = inject(MauiDeviceMgmtService);
  private readonly dialogService = inject(MauiDialogService);
  private readonly cdr = inject(ChangeDetectorRef);

  private readonly subs = new Subscription();

  serialNumber = '';
  deviceConnected = false;
  deviceProgrammed = false;
  ftdiPid = '';
  statusMessage = 'Scanning USB bus for connected Maui device...';

  // 3-second stabilization delay between operator button click and actual test execution
  // to allow physical connections, voltage rails, and USB enumeration to settle.
  stabilizationDelayMs = 3000;

  // Stage 1: Host + DUT + Charger Verification (PD & Data Path)
  stage1InProgress = false;
  stage1Passed = false;
  stage1StatusValue: 'NOT_RUN' | 'PASSED' | 'FAILED' | 'BYPASSED' = 'NOT_RUN';
  stage1Orientation = '';
  h2hLinkSpeed = '';
  h2hSerialMatched = false;
  adbConnected = false;
  adbDutSpeed = '';
  apUartOk = false;
  ecUartOk = false;
  gscUartOk = false;
  apVersionStr = '';
  ecVersionStr = '';
  gscVersionStr = '';
  negotiatedVoltage = 0;
  negotiatedCurrent = 0;
  measuredVbusMv = 0;
  calculatedPowerW = 0;

  // Stage 2: 180° DUT Cable Flip Polarity Verification (With Charger)
  stage2InProgress = false;
  stage2Passed = false;
  stage2StatusValue: 'NOT_RUN' | 'PASSED' | 'FAILED' | 'BYPASSED' = 'NOT_RUN';
  dutOrientation = '';

  // Stage 3: Charger Disconnect Verification (No Charger)
  stage3InProgress = false;
  stage3Passed = false;
  stage3StatusValue: 'NOT_RUN' | 'PASSED' | 'FAILED' | 'BYPASSED' = 'NOT_RUN';
  chargerDisconnected = false;

  // Stage 4: Log Saving & Finalization
  savingLogs = false;
  logsSaved = false;

  @ViewChild('stage1Status') stage1Status!: TestPassFailButton;
  @ViewChild('stage2Status') stage2Status!: TestPassFailButton;
  @ViewChild('stage3Status') stage3Status!: TestPassFailButton;
  @ViewChild('completeStatus') completeStatus!: TestPassFailButton;

  @ViewChild('stage1Btn', {read: ElementRef})
  stage1Btn?: ElementRef<HTMLButtonElement>;
  @ViewChild('stage2Btn', {read: ElementRef})
  stage2Btn?: ElementRef<HTMLButtonElement>;
  @ViewChild('stage3Btn', {read: ElementRef})
  stage3Btn?: ElementRef<HTMLButtonElement>;
  @ViewChild('saveDutLogsBtn', {read: ElementRef})
  saveDutLogsBtn?: ElementRef<HTMLButtonElement>;

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
          }
          setTimeout(() => this.stage1Btn?.nativeElement?.focus(), 200);
          this.cdr.detectChanges();
        },
        error: err => {
          console.warn('Could not auto-read connected serial on init:', err);
        },
      }),
    );
  }

  ngOnDestroy(): void {
    this.subs.unsubscribe();
  }

  // 1. Stage 1: Host + DUT + Charger Verification (With Charger)
  runStage1WithCharger(): void {
    this.stage1InProgress = true;
    this.stage1Status?.showWorking();
    this.cdr.detectChanges();

    // 3-second stabilization delay between operator button click and actual test execution
    // to allow physical connections, voltage rails, and USB enumeration to settle.
    this.subs.add(
      timer(this.stabilizationDelayMs)
        .pipe(
          switchMap(() =>
            this.mauiService.runHostDutTests(
              this.serialNumber,
              this.serialNumber,
              'stage1_with_charger',
            ),
          ),
        )
        .subscribe({
          next: res => {
            this.stage1InProgress = false;
            this.h2hLinkSpeed = res.h2hSpeed5gbps
              ? '5 Gbps (SuperSpeed)'
              : 'Non-SuperSpeed';
            this.h2hSerialMatched = res.h2hSerialMatched;
            this.adbConnected = res.adbConnected;
            this.adbDutSpeed = res.adbDutSpeed;
            this.apUartOk = res.apUartOk;
            this.ecUartOk = res.ecUartOk;
            this.gscUartOk = res.gscUartOk;
            this.apVersionStr = res.apVersionStr;
            this.ecVersionStr = res.ecVersionStr;
            this.gscVersionStr = res.gscVersionStr;
            this.negotiatedVoltage = res.negotiatedVoltageV;
            this.negotiatedCurrent = res.negotiatedCurrentA;
            this.measuredVbusMv = res.measuredVbusMv;
            this.calculatedPowerW =
              res.calculatedPowerW ||
              Math.round(res.negotiatedVoltageV * res.negotiatedCurrentA);
            this.stage1Orientation = res.dutOrientation;
            this.dutOrientation = res.dutOrientation;

            this.stage1Passed = res.allPassed;

            if (this.stage1Passed) {
              this.stage1StatusValue = 'PASSED';
              this.stage1Status?.showPassed();
              autoAdvanceToCard('dut-card-2', this.stage2Btn);
            } else {
              this.stage1StatusValue = 'FAILED';
              this.stage1Status?.showFailed();
              this.dialogService.openFailure(
                'Stage 1: Host + DUT + Charger Verification Failed',
                res.summary ||
                  'H2H link, CCD consoles, or PD charger contract verification failed.',
                () => this.runStage1WithCharger(),
                () => this.bypassStage1(),
              );
            }
            this.cdr.detectChanges();
          },
          error: err => {
            this.stage1InProgress = false;
            this.stage1StatusValue = 'FAILED';
            this.stage1Status?.showFailed();
            this.dialogService.openFailure(
              'Stage 1: Host + DUT + Charger Error',
              err,
              () => this.runStage1WithCharger(),
              () => this.bypassStage1(),
            );
            this.cdr.detectChanges();
          },
        }),
    );
  }

  // 2. Stage 2: 180° DUT Cable Flip Polarity Verification (With Charger)
  runStage2SbuFlip(): void {
    this.stage2InProgress = true;
    this.stage2Status?.showWorking();
    this.cdr.detectChanges();

    // 3-second stabilization delay between operator button click and actual test execution
    // to allow physical connections, voltage rails, and USB enumeration to settle.
    this.subs.add(
      timer(this.stabilizationDelayMs)
        .pipe(
          switchMap(() =>
            this.mauiService.runHostDutTests(
              this.serialNumber,
              this.serialNumber,
              'stage2_sbu_flip',
              this.stage1Orientation,
            ),
          ),
        )
        .subscribe({
          next: res => {
            this.stage2InProgress = false;
            this.stage2Passed = res.allPassed;
            this.dutOrientation = res.dutOrientation;

            if (this.stage2Passed) {
              this.stage2StatusValue = 'PASSED';
              this.stage2Status?.showPassed();
              autoAdvanceToCard('dut-card-3', this.stage3Btn);
            } else {
              this.stage2StatusValue = 'FAILED';
              this.stage2Status?.showFailed();
              this.dialogService.openFailure(
                'Stage 2: 180° Cable Flip Verification Failed',
                res.summary ||
                  `SBU orientation or link recovery failed. Orientation: ${res.dutOrientation}.`,
                () => this.runStage2SbuFlip(),
                () => this.bypassStage2(),
              );
            }
            this.cdr.detectChanges();
          },
          error: err => {
            this.stage2InProgress = false;
            this.stage2StatusValue = 'FAILED';
            this.stage2Status?.showFailed();
            this.dialogService.openFailure(
              'Stage 2: 180° Cable Flip Error',
              err,
              () => this.runStage2SbuFlip(),
              () => this.bypassStage2(),
            );
            this.cdr.detectChanges();
          },
        }),
    );
  }

  // 3. Stage 3: Charger Disconnect Verification (No Charger)
  runStage3NoCharger(): void {
    this.stage3InProgress = true;
    this.stage3Status?.showWorking();
    this.cdr.detectChanges();

    // 3-second stabilization delay between operator button click and actual test execution
    // to allow physical connections, voltage rails, and USB enumeration to settle.
    this.subs.add(
      timer(this.stabilizationDelayMs)
        .pipe(
          switchMap(() =>
            this.mauiService.runHostDutTests(
              this.serialNumber,
              this.serialNumber,
              'stage3_no_charger',
            ),
          ),
        )
        .subscribe({
          next: res => {
            this.stage3InProgress = false;
            this.chargerDisconnected = res.chargerDisconnectedOk;
            this.stage3Passed = res.allPassed;

            if (this.stage3Passed) {
              this.stage3StatusValue = 'PASSED';
              this.stage3Status?.showPassed();
              autoAdvanceToCard('dut-card-4', this.saveDutLogsBtn);
              setTimeout(() => this.saveDutLogs(), 700);
            } else {
              this.stage3StatusValue = 'FAILED';
              this.stage3Status?.showFailed();
              this.dialogService.openFailure(
                'Stage 3: Charger Disconnect Verification Failed',
                res.summary ||
                  'Charger disconnection check failed (Plug still detected).',
                () => this.runStage3NoCharger(),
                () => this.bypassStage3(),
              );
            }
            this.cdr.detectChanges();
          },
          error: err => {
            this.stage3InProgress = false;
            this.stage3StatusValue = 'FAILED';
            this.stage3Status?.showFailed();
            this.dialogService.openFailure(
              'Stage 3: Charger Disconnect Error',
              err,
              () => this.runStage3NoCharger(),
              () => this.bypassStage3(),
            );
            this.cdr.detectChanges();
          },
        }),
    );
  }

  bypassStage1(): void {
    this.stage1Passed = true;
    if (this.stage1StatusValue === 'FAILED') {
      this.stage1Status?.showFailed();
    } else {
      this.stage1StatusValue = 'BYPASSED';
      this.stage1Status?.showBypassed();
    }
    autoAdvanceToCard('dut-card-2', this.stage2Btn);
    this.cdr.detectChanges();
  }

  bypassStage2(): void {
    this.stage2Passed = true;
    if (this.stage2StatusValue === 'FAILED') {
      this.stage2Status?.showFailed();
    } else {
      this.stage2StatusValue = 'BYPASSED';
      this.stage2Status?.showBypassed();
    }
    autoAdvanceToCard('dut-card-3', this.stage3Btn);
    this.cdr.detectChanges();
  }

  bypassStage3(): void {
    this.stage3Passed = true;
    if (this.stage3StatusValue === 'FAILED') {
      this.stage3Status?.showFailed();
    } else {
      this.stage3StatusValue = 'BYPASSED';
      this.stage3Status?.showBypassed();
    }
    autoAdvanceToCard('dut-card-4', this.saveDutLogsBtn);
    this.cdr.detectChanges();
  }

  // 4. Stage 4: Save Host+DUT Test Logs & Complete
  saveDutLogs(): void {
    this.savingLogs = true;
    this.completeStatus?.showWorking();
    this.cdr.detectChanges();

    const allPassed =
      this.stage1StatusValue === 'PASSED' &&
      this.stage2StatusValue === 'PASSED' &&
      this.stage3StatusValue === 'PASSED';
    const overallVerdictTag = allPassed
      ? 'PASSED'
      : this.stage1StatusValue === 'FAILED' ||
          this.stage2StatusValue === 'FAILED' ||
          this.stage3StatusValue === 'FAILED'
        ? 'FAILED'
        : 'BYPASSED';

    const logSummary = [
      `OVERALL_DUT_STATUS: ${overallVerdictTag}`,
      `ALL_PASSED=${allPassed}`,
      `Stage 1 (Host+DUT Link & CCD): ${this.stage1StatusValue}`,
      `Stage 2 (PD 20V/100W Contract & Power): ${this.stage2StatusValue} (${this.negotiatedVoltage}V @ ${this.negotiatedCurrent}A, ${this.calculatedPowerW}W)`,
      `Stage 3 (180° SBU Cable Flip): ${this.stage3StatusValue} (Orientation: ${this.dutOrientation})`,
    ].join('\n');

    this.subs.add(
      this.deviceMgmtService
        .saveCardLog(
          this.serialNumber || 'UNKNOWN_DEVICE',
          'host_dut_tests',
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
              'Failed to save DUT logs',
              err,
              () => this.saveDutLogs(),
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
    this.saveDutLogs();
  }
}
