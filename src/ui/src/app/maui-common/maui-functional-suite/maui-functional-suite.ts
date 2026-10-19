// Copyright 2026 The ChromiumOS Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

import {
  ChangeDetectorRef,
  Component,
  ElementRef,
  HostListener,
  Input,
  OnDestroy,
  OnInit,
  ViewChild,
  inject,
} from '@angular/core';
import {CommonModule} from '@angular/common';
import {FormsModule} from '@angular/forms';
import {MatButtonModule} from '@angular/material/button';
import {MatCheckboxModule} from '@angular/material/checkbox';
import {MatFormFieldModule} from '@angular/material/form-field';
import {MatIconModule} from '@angular/material/icon';
import {MatInputModule} from '@angular/material/input';
import {
  EMPTY,
  Subscription,
  catchError,
  exhaustMap,
  switchMap,
  timer,
} from 'rxjs';
import {DiagnosemeReworkCard} from '../../diagnoseme-rework-card/diagnoseme-rework-card';
import {DiagnosemeReworkCardColumn} from '../../diagnoseme-rework-card-column/diagnoseme-rework-card-column';
import {MauiDialogService} from '../maui-dialog.service';
import {autoAdvanceToCard} from '../maui-scroll.utils';
import {MauiStation2Service} from '../../services/maui-station2.service';
import {MauiDeviceMgmtService} from '../../services/maui-device-mgmt.service';
import {TestPassFailButton} from '../../test-pass-fail-button/test-pass-fail-button';
import {
  MauiAdbTransferTestResponse,
  MauiBslButtonProgress,
  MauiLogFileInfo,
} from '../../../proto/maui-manufacturing.pb';
import {MauiHwRev, MauiHwRevProfile, mauiHwRevProfile} from '../maui-hw-rev';
import {
  MauiSerialProvisionCardComponent,
  MauiSerialProvisionStatus,
} from '../maui-serial-provision-card/maui-serial-provision-card';
import {
  MauiH2hSpiCheckCardComponent,
  MauiH2hSpiCheckStatus,
} from '../maui-h2h-spi-check-card/maui-h2h-spi-check-card';

@Component({
  selector: 'app-maui-functional-suite',
  standalone: true,
  templateUrl: './maui-functional-suite.html',
  styleUrl: './maui-functional-suite.scss',
  imports: [
    CommonModule,
    DiagnosemeReworkCard,
    DiagnosemeReworkCardColumn,
    FormsModule,
    MatButtonModule,
    MatCheckboxModule,
    MatFormFieldModule,
    MatIconModule,
    MatInputModule,
    MauiH2hSpiCheckCardComponent,
    MauiSerialProvisionCardComponent,
    TestPassFailButton,
  ],
})
export class MauiFunctionalSuiteComponent implements OnInit, OnDestroy {
  @Input() mode: 'smt' | 'fatp_qt0' = 'smt';
  @Input() suiteTitle = 'Maui Functional Verification';
  @Input() suiteSubtitle =
    'System-level functional testing for Maui hardware verification.';
  @Input() nextStageRoute = '/maui/v1.1/labeling';
  @Input() nextStageLabel = 'Go to Next Stage';
  /** Hardware revision of the board under test (texts and extra cards). */
  @Input() hwRev: MauiHwRev = 'v1.1';

  private readonly mauiService = inject(MauiStation2Service);
  private readonly deviceMgmtService = inject(MauiDeviceMgmtService);
  private readonly dialogService = inject(MauiDialogService);
  private readonly cdr = inject(ChangeDetectorRef);

  private readonly subs = new Subscription();

  serialNumber = '';
  lastTestedSerial = '';
  devicePort = '';
  deviceConnected = false;
  deviceProgrammed = false;
  ftdiPid = '';
  statusMessage = 'Scanning USB bus for connected Maui device...';

  get canSaveLogs(): boolean {
    return (
      this.stage3Passed &&
      !this.deviceConnected &&
      !this.savingLogs &&
      !this.logsSaved
    );
  }

  // 3-second stabilization delay between operator button click and actual test execution
  stabilizationDelayMs = 3000;

  // Barcode scanner buffer (for FATP QT0 mode)
  private barcodeBuffer = '';
  private lastBarcodeKeyTime = 0;
  scannedBarcode = '';
  barcodeMatches = false;
  /** The operator bypassed the barcode check (barcodeMatches only unlocks). */
  barcodeBypassed = false;
  barcodeCheckInProgress = false;
  barcodeCheckFailed = false;
  barcodeMessage = '';
  adbSerial = '';
  ftdiSerial = '';
  dualEndpointsMatch = false;

  // Card 1: Host USB & Console Check
  usbCheckInProgress = false;
  usbCheckPassed = false;
  usbStatusValue: 'NOT_RUN' | 'PASSED' | 'FAILED' | 'BYPASSED' = 'NOT_RUN';
  usbSummary = '';
  mcuVersionStr = '';
  pdcAppConfigStr = '';
  card1Orientation = '';
  hostOrientation = '';
  card1Hwrev: number | null = null;
  card1HwrevOk = false;
  card1ExpectedHwrev = '';
  card1TempC: number | null = null;
  card1TempOk = false;
  card1GpiosOk = false;
  card1GpioDetails = '';
  card1VoltagesOk = false;
  card1Pp5000Mv = 0;
  card1HostVbusMv = 0;
  card1ChgVbusMv = 0;
  card1VoltageDetails = '';

  // Card 2: Host Cable 180° Flip Test
  replugInProgress = false;
  replugPassed = false;
  replugStatusValue: 'NOT_RUN' | 'PASSED' | 'FAILED' | 'BYPASSED' = 'NOT_RUN';
  replugMessage = '';
  card2Orientation = '';
  card2Hwrev: number | null = null;
  card2HwrevOk = false;
  card2ExpectedHwrev = '';
  card2TempC: number | null = null;
  card2TempOk = false;
  card2GpiosOk = false;
  card2GpioDetails = '';
  card2VoltagesOk = false;
  card2Pp5000Mv = 0;
  card2HostVbusMv = 0;
  card2ChgVbusMv = 0;
  card2VoltageDetails = '';

  // Card 3: RESET Button Test
  resetBaselineInProgress = false;
  resetBaselineCaptured = false;
  initialUptimeMs = 0;
  resetVerifyInProgress = false;
  resetPassed = false;
  resetStatusValue: 'NOT_RUN' | 'PASSED' | 'FAILED' | 'BYPASSED' = 'NOT_RUN';
  resetMessage = '';

  // Card 4: BSL Button Test (v1.1 ADC Status, v1.2 guided GPIO check)
  bslAdcInProgress = false;
  bslPassed = false;
  bslStatusValue: 'NOT_RUN' | 'PASSED' | 'FAILED' | 'BYPASSED' = 'NOT_RUN';
  peakBslMv = 0;
  bslMessage = '';
  /** v1.2 guided check: running step (1-3), 0 when not running. */
  bslPhase = 0;
  /** v1.2 guided check: live operator prompt. */
  bslPrompt = '';
  /** v1.2 guided check: countdown of the running step. */
  bslSecondsLeft = 0;
  /** v1.2 guided check: last GPIO level (-1 unknown). */
  bslLevel = -1;
  /** v1.2 guided check: step that failed (0: none). */
  bslFailedPhase = 0;

  // Card 5: Visual LED Check
  ledAmberChecked = false;
  ledGreenChecked = false;
  ledPassed = false;
  ledStatusValue: 'NOT_RUN' | 'PASSED' | 'FAILED' | 'BYPASSED' = 'NOT_RUN';

  // Card 6: Host + DUT + Charger Verification (PD & Data Path)
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

  // Card 7: 180° DUT Cable Flip Polarity Verification
  stage2InProgress = false;
  stage2Passed = false;
  stage2StatusValue: 'NOT_RUN' | 'PASSED' | 'FAILED' | 'BYPASSED' = 'NOT_RUN';
  dutOrientation = '';

  // Card 9: Charger Disconnect Verification (No Charger, after the ADB card)
  stage3InProgress = false;

  /** True while any card of the suite is running. */
  get anyTestInProgress(): boolean {
    return (
      this.barcodeCheckInProgress ||
      this.usbCheckInProgress ||
      this.replugInProgress ||
      this.resetBaselineInProgress ||
      this.resetVerifyInProgress ||
      this.bslAdcInProgress ||
      this.stage1InProgress ||
      this.stage2InProgress ||
      this.stage3InProgress ||
      this.adbInProgress ||
      this.serialProvisionInProgress ||
      this.h2hSpiInProgress
    );
  }
  stage3Passed = false;
  stage3StatusValue: 'NOT_RUN' | 'PASSED' | 'FAILED' | 'BYPASSED' = 'NOT_RUN';
  chargerDisconnected = false;

  // Card 8: ADB Data Transfer Reliability (DUT through the H2H bridge, right
  // after the SBU flip card with Host + DUT + charger connected)
  adbInProgress = false;
  adbPassed = false;
  adbStatusValue: 'NOT_RUN' | 'PASSED' | 'FAILED' | 'BYPASSED' = 'NOT_RUN';
  adbTransfer: MauiAdbTransferTestResponse | null = null;

  // Card 10: Log Saving & Finalization
  savingLogs = false;
  logsSaved = false;
  logFiles: MauiLogFileInfo[] = [];
  logsDirectory = '';
  overallLogsVerdict = '';
  logsInspected = false;
  inspectingLogs = false;
  finalMesTrackingId = '';
  finalSummaryPath = '';
  /** Verdict of the saved run: every card PASSED (bypassed = not passed). */
  finalPassed = false;
  /** Cards that did not pass, as shown on the final card ("<card>: <status>"). */
  finalNotPassedCards: string[] = [];

  @ViewChild('barcodeStatus') barcodeStatus?: TestPassFailButton;
  @ViewChild('usbCheckStatus') usbCheckStatus!: TestPassFailButton;
  @ViewChild('replugStatus') replugStatus!: TestPassFailButton;
  @ViewChild('resetStatus') resetStatus!: TestPassFailButton;
  @ViewChild('bslStatus') bslStatus!: TestPassFailButton;
  @ViewChild('ledStatus') ledStatus!: TestPassFailButton;
  @ViewChild('stage1Status') stage1Status!: TestPassFailButton;
  @ViewChild('stage2Status') stage2Status!: TestPassFailButton;
  @ViewChild('stage3Status') stage3Status!: TestPassFailButton;
  @ViewChild('adbStatus') adbStatus!: TestPassFailButton;
  @ViewChild('completeStatus') completeStatus!: TestPassFailButton;

  @ViewChild('runUsbCheckBtn', {read: ElementRef})
  runUsbCheckBtn?: ElementRef<HTMLButtonElement>;
  @ViewChild('replugBtn', {read: ElementRef})
  replugBtn?: ElementRef<HTMLButtonElement>;
  @ViewChild('captureResetBtn', {read: ElementRef})
  captureResetBtn?: ElementRef<HTMLButtonElement>;
  @ViewChild('readBslBtn', {read: ElementRef})
  readBslBtn?: ElementRef<HTMLButtonElement>;
  @ViewChild('confirmLedsBtn', {read: ElementRef})
  confirmLedsBtn?: ElementRef<HTMLButtonElement>;
  @ViewChild('stage1Btn', {read: ElementRef})
  stage1Btn?: ElementRef<HTMLButtonElement>;
  @ViewChild('stage2Btn', {read: ElementRef})
  stage2Btn?: ElementRef<HTMLButtonElement>;
  @ViewChild('stage3Btn', {read: ElementRef})
  stage3Btn?: ElementRef<HTMLButtonElement>;
  @ViewChild('adbTransferBtn', {read: ElementRef})
  adbTransferBtn?: ElementRef<HTMLButtonElement>;
  @ViewChild('saveLogsBtn', {read: ElementRef})
  saveLogsBtn?: ElementRef<HTMLButtonElement>;
  @ViewChild('barcodeInput', {read: ElementRef})
  barcodeInput?: ElementRef<HTMLInputElement>;

  get isFatpMode(): boolean {
    return this.mode === 'fatp_qt0';
  }

  get rev(): MauiHwRevProfile {
    return mauiHwRevProfile(this.hwRev);
  }

  // Serial Number Provisioning (Maui v1.2 only)
  serialProvisionStatus: MauiSerialProvisionStatus = 'NOT_RUN';
  serialProvisionInProgress = false;
  /** Last result of the serial card (logged when it is bypassed). */
  private serialProvisionLastResult = '';

  /** True when the serial card is not needed or passed / was bypassed. */
  get serialStepDone(): boolean {
    return (
      !this.rev.requiresSerialProvisioning ||
      this.serialProvisionStatus === 'PASSED' ||
      this.serialProvisionStatus === 'BYPASSED'
    );
  }

  // H2H SPI Flash Access Check (Maui v1.2 only)
  h2hSpiStatus: MauiH2hSpiCheckStatus = 'NOT_RUN';
  h2hSpiSerial = '';
  h2hSpiInProgress = false;
  /** Last result of the H2H SPI card (logged when it is bypassed). */
  private h2hSpiLastResult = '';

  /** True when the H2H SPI card is not needed or passed / was bypassed. */
  get h2hSpiStepDone(): boolean {
    return (
      !this.rev.requiresH2hSpiCheck ||
      this.h2hSpiStatus === 'PASSED' ||
      this.h2hSpiStatus === 'BYPASSED'
    );
  }

  /** Card 1 and the rest of the suite need the v1.2 serial cards done. */
  get serialCardsDone(): boolean {
    return this.serialStepDone && this.h2hSpiStepDone;
  }

  /** Number shown for functional card |n| (1 = Host USB & Console). */
  cardNo(n: number): number {
    return n + this.rev.cardNumberOffset;
  }

  /** v1.2: the serial and H2H SPI cards come first ("1." and "2."). */
  get serialCardTitle(): string {
    return `${this.cardNo(-1)}. Serial Number Provisioning (H2H → MCU)`;
  }

  get h2hSpiCardTitle(): string {
    return `${this.cardNo(0)}. H2H Flash Access Check (SPI)`;
  }

  get bslCardTitle(): string {
    return this.rev.bslMethod === 'gpio'
      ? `${this.cardNo(4)}. Interactive BSL Button Test (GPIO)`
      : `${this.cardNo(4)}. Interactive BSL Button Test (ADC Status)`;
  }

  get bslReadLabel(): string {
    return this.rev.bslMethod === 'gpio' ? 'Start BSL Check' : 'Read BSL ADC';
  }

  /** CSS state of step |n| (1-3) of the guided v1.2 BSL check. */
  bslStepState(n: number): 'active' | 'done' | 'failed' | '' {
    if (this.bslFailedPhase) {
      return n === this.bslFailedPhase
        ? 'failed'
        : n < this.bslFailedPhase
          ? 'done'
          : '';
    }
    if (this.bslPassed && this.bslStatusValue === 'PASSED') {
      return 'done';
    }
    if (!this.bslAdcInProgress || !this.bslPhase) {
      return '';
    }
    return n === this.bslPhase ? 'active' : n < this.bslPhase ? 'done' : '';
  }

  /**
   * One-line result of the ADB transfer card, e.g. "transfer 25.1 s / limit
   * 40 s, 2 files 800 MiB, push 57.0 MB/s, pull 88.0 MB/s, link host/DUT
   * 5000 Mb/s". Fields missing in older backends are left out.
   */
  adbSummary(t: MauiAdbTransferTestResponse): string {
    const link = t as unknown as {
      hostLinkMbps?: number | string;
      dutLinkMbps?: number | string;
    };
    const parts: string[] = [];
    if (t.transferSeconds) {
      parts.push(
        `transfer ${t.transferSeconds.toFixed(1)} s` +
          (t.deadlineSeconds
            ? ` / limit ${t.deadlineSeconds.toFixed(0)} s`
            : ''),
      );
    }
    const bytes = Number(t.fileSizeBytes) || 0;
    if (bytes > 0) {
      parts.push(`2 files ${(bytes / 2 ** 20).toFixed(0)} MiB`);
    }
    parts.push(
      `push ${(t.pushMbPerS || 0).toFixed(1)} MB/s`,
      `pull ${(t.pullMbPerS || 0).toFixed(1)} MB/s`,
    );
    const host = Number(link.hostLinkMbps) || 0;
    const dut = Number(link.dutLinkMbps) || 0;
    if (host || dut) {
      const speeds = host === dut ? `${host}` : `${host || '?'}/${dut || '?'}`;
      parts.push(`link host/DUT ${speeds} Mb/s`);
    }
    return parts.join(', ');
  }

  onSerialProvisionStatus(event: {
    status: MauiSerialProvisionStatus;
    serial: string;
    message?: string;
  }): void {
    this.serialProvisionStatus = event.status;
    if (event.status === 'BYPASSED') {
      this.logBypass(
        'mcu_serial_provisioning',
        'MCU Serial Provisioning',
        this.serialProvisionLastResult,
      );
    } else {
      this.serialProvisionLastResult = event.message ?? '';
    }
    if (event.serial && event.status === 'PASSED') {
      this.serialNumber = event.serial;
      this.lastTestedSerial = event.serial;
    }
    if (this.serialStepDone) {
      autoAdvanceToCard(
        this.h2hSpiStepDone ? 'func-card-1' : 'func-card-h2h-spi',
      );
    }
    this.cdr.detectChanges();
  }

  onH2hSpiStatus(event: {
    status: MauiH2hSpiCheckStatus;
    serial: string;
    message?: string;
  }): void {
    this.h2hSpiStatus = event.status;
    this.h2hSpiSerial = event.serial;
    if (event.status === 'BYPASSED') {
      this.logBypass(
        'h2h_spi_check',
        'H2H SPI Flash Access',
        this.h2hSpiLastResult,
      );
    } else {
      this.h2hSpiLastResult = event.message ?? '';
    }
    if (this.serialCardsDone) {
      autoAdvanceToCard('func-card-1');
    }
    this.cdr.detectChanges();
  }

  ngOnInit(): void {
    this.pollDeviceDetection();
  }

  ngOnDestroy(): void {
    this.subs.unsubscribe();
  }

  @HostListener('window:keydown', ['$event'])
  handleKeyboardEvent(event: KeyboardEvent): void {
    if (!this.isFatpMode || this.barcodeMatches) {
      return;
    }
    const currentTime = Date.now();
    if (currentTime - this.lastBarcodeKeyTime > 100) {
      this.barcodeBuffer = '';
    }
    this.lastBarcodeKeyTime = currentTime;

    if (event.key === 'Enter') {
      if (this.barcodeBuffer.trim().length > 3) {
        this.scannedBarcode = this.barcodeBuffer.trim();
        this.barcodeBuffer = '';
        this.verifyBarcode();
        event.preventDefault();
      }
    } else if (event.key.length === 1) {
      this.barcodeBuffer += event.key;
    }
  }

  pollDeviceDetection(): void {
    this.subs.add(
      timer(0, 1500)
        .pipe(
          // A failed poll must not end the polling: Card 10 waits for the
          // operator to unplug the unit.
          exhaustMap(() =>
            this.deviceMgmtService.readConnectedDeviceSerial().pipe(
              catchError(() => {
                this.statusMessage = 'Device detection error.';
                this.cdr.detectChanges();
                return EMPTY;
              }),
            ),
          ),
        )
        .subscribe({
          next: res => {
            this.deviceConnected = res.isConnected;
            if (res.isConnected && res.serialNumber) {
              this.serialNumber = res.serialNumber;
              this.lastTestedSerial = res.serialNumber;
            }
            if (res.devicePort) {
              this.devicePort = res.devicePort;
            }
            this.deviceProgrammed = !!res.ftdiIsProgrammed;
            this.ftdiPid = res.ftdiPid ? '0x' + res.ftdiPid.toString(16) : '';
            if (res.adbSerial) {
              this.adbSerial = res.adbSerial;
            }
            if (res.ftdiSerial) {
              this.ftdiSerial = res.ftdiSerial;
            }
            this.dualEndpointsMatch = res.dualEndpointsMatch;

            if (this.deviceConnected) {
              if (this.deviceProgrammed) {
                this.statusMessage = `Connected Maui Device: ${this.serialNumber || this.lastTestedSerial} (${this.devicePort})`;
              } else {
                this.statusMessage = `Unprogrammed Device Detected: ${this.serialNumber || 'Unknown'} (${this.rev.consoleLabel} PID: ${this.ftdiPid}) — ${this.rev.unprovisionedHint}`;
              }
            } else {
              this.statusMessage =
                'No Maui Device Detected. Please connect Maui Host USB-C cable.';
            }
            this.cdr.detectChanges();
          },
        }),
    );
  }

  refreshDeviceStatus(): void {
    const sub = this.deviceMgmtService
      .readConnectedDeviceSerial()
      .subscribe(res => {
        this.deviceConnected = res.isConnected;
        this.serialNumber = res.serialNumber;
        this.devicePort = res.devicePort || this.rev.defaultConsolePort;
        this.deviceProgrammed = !!res.ftdiIsProgrammed;
        this.ftdiPid = res.ftdiPid ? '0x' + res.ftdiPid.toString(16) : '';
        this.adbSerial = res.adbSerial;
        this.ftdiSerial = res.ftdiSerial;
        this.dualEndpointsMatch = res.dualEndpointsMatch;
        this.cdr.detectChanges();
      });
    this.subs.add(sub);
  }

  // Card 0: Barcode Verification (FATP QT0)
  verifyBarcode(): void {
    if (!this.scannedBarcode.trim()) {
      return;
    }
    this.barcodeCheckInProgress = true;
    this.barcodeCheckFailed = false;
    this.barcodeStatus?.showWorking();

    const sub = this.deviceMgmtService
      .verifyScannedLabel(
        this.serialNumber,
        this.scannedBarcode.trim(),
        this.adbSerial,
        this.ftdiSerial,
      )
      .subscribe({
        next: res => {
          this.barcodeCheckInProgress = false;
          this.barcodeMatches = res.matches;
          this.barcodeMessage = res.message;
          if (res.matches) {
            this.barcodeStatus?.showPassed();
            autoAdvanceToCard('func-card-1', this.runUsbCheckBtn);
          } else {
            this.barcodeCheckFailed = true;
            this.barcodeStatus?.showFailed();
            this.dialogService.openFailure(
              'Barcode Serial Mismatch',
              res.message,
              () => this.verifyBarcode(),
              () => {
                this.barcodeMatches = true;
                this.barcodeBypassed = true;
                this.barcodeMessage =
                  'Barcode verification bypassed by operator.';
                this.barcodeStatus?.showBypassed();
                autoAdvanceToCard('func-card-1', this.runUsbCheckBtn);
                this.cdr.detectChanges();
              },
            );
          }
          this.cdr.detectChanges();
        },
        error: err => {
          this.barcodeCheckInProgress = false;
          this.barcodeCheckFailed = true;
          this.barcodeStatus?.showFailed();
          this.dialogService.openFailure(
            'Barcode Verification Error',
            err,
            () => this.verifyBarcode(),
            () => {
              this.barcodeMatches = true;
              this.barcodeBypassed = true;
              this.barcodeStatus?.showBypassed();
              autoAdvanceToCard('func-card-1', this.runUsbCheckBtn);
              this.cdr.detectChanges();
            },
          );
          this.cdr.detectChanges();
        },
      });
    this.subs.add(sub);
  }

  bypassBarcode(): void {
    this.barcodeMatches = true;
    this.barcodeBypassed = true;
    this.barcodeMessage = 'Barcode verification bypassed by operator.';
    this.barcodeStatus?.showBypassed();
    autoAdvanceToCard('func-card-1', this.runUsbCheckBtn);
    this.cdr.detectChanges();
  }

  // Card 1: Host USB & Console Check
  runUsbCheck(): void {
    this.usbCheckInProgress = true;
    this.usbCheckStatus.showWorking();
    this.cdr.detectChanges();

    const sub = timer(this.stabilizationDelayMs)
      .pipe(
        switchMap(() =>
          this.mauiService.runHostOnlyTests(this.serialNumber, this.devicePort),
        ),
      )
      .subscribe({
        next: res => {
          this.usbCheckInProgress = false;
          this.usbCheckPassed = res.allPassed;
          this.usbSummary = res.summary || '';
          this.mcuVersionStr = res.mcuVersionStr || '';
          this.pdcAppConfigStr = res.pdcAppconfigVer || '';
          this.card1Orientation = res.hostOrientation || '';
          this.hostOrientation = res.hostOrientation || '';
          this.card1Hwrev =
            res.detectedHwrev !== undefined ? res.detectedHwrev : null;
          this.card1HwrevOk = res.hwrevOk;
          this.card1ExpectedHwrev =
            res.expectedHwrev || this.rev.expectedHwrevText;
          this.card1TempC =
            res.temperatureC !== undefined ? res.temperatureC : null;
          this.card1TempOk = res.tempOk;
          this.card1GpiosOk = res.gpiosOk;
          this.card1GpioDetails = res.gpioDetails || '';
          this.card1VoltagesOk = res.voltagesOk;
          this.card1Pp5000Mv = res.pp5000Mv || 0;
          this.card1HostVbusMv = res.hostVbusMv || 0;
          this.card1ChgVbusMv = res.chgVbusMv || 0;
          this.card1VoltageDetails = res.voltageDetails || '';

          if (res.allPassed) {
            this.usbStatusValue = 'PASSED';
            this.usbCheckStatus.showPassed();
            autoAdvanceToCard('func-card-2', this.replugBtn);
          } else {
            this.usbStatusValue = 'FAILED';
            this.usbCheckStatus.showFailed();
            this.dialogService.openFailure(
              'Host USB & Console Check Failed',
              res.summary || 'Host USB or PDC check failed.',
              () => this.runUsbCheck(),
              () => {
                this.usbCheckPassed = true;
                this.usbStatusValue = 'BYPASSED';
                this.usbCheckStatus.showBypassed();
                autoAdvanceToCard('func-card-2', this.replugBtn);
                this.cdr.detectChanges();
              },
            );
          }
          this.cdr.detectChanges();
        },
        error: err => {
          this.usbCheckInProgress = false;
          this.usbStatusValue = 'FAILED';
          this.usbCheckStatus.showFailed();
          this.dialogService.openFailure(
            'Host USB & Console Check Error',
            err,
            () => this.runUsbCheck(),
            () => {
              this.usbCheckPassed = true;
              this.usbStatusValue = 'BYPASSED';
              this.usbCheckStatus.showBypassed();
              autoAdvanceToCard('func-card-2', this.replugBtn);
              this.cdr.detectChanges();
            },
          );
          this.cdr.detectChanges();
        },
      });
    this.subs.add(sub);
  }

  bypassUsbCheck(): void {
    this.usbCheckPassed = true;
    this.usbStatusValue = 'BYPASSED';
    this.usbCheckStatus.showBypassed();
    autoAdvanceToCard('func-card-2', this.replugBtn);
    this.cdr.detectChanges();
  }

  // Card 2: Host Cable 180° Flip Test
  runHostReplug(): void {
    this.replugInProgress = true;
    this.replugStatus.showWorking();
    this.cdr.detectChanges();

    const sub = timer(this.stabilizationDelayMs)
      .pipe(
        switchMap(() =>
          this.mauiService.testHostReplug(
            this.serialNumber,
            this.devicePort,
            this.card1Orientation,
          ),
        ),
      )
      .subscribe({
        next: res => {
          this.replugInProgress = false;
          this.replugPassed = res.allPassed;
          this.replugMessage = res.message || '';
          this.card2Orientation = res.hostOrientation || '';
          this.card2Hwrev =
            res.detectedHwrev !== undefined ? res.detectedHwrev : null;
          this.card2HwrevOk = res.hwrevOk;
          this.card2ExpectedHwrev =
            res.expectedHwrev || this.rev.expectedHwrevText;
          this.card2TempC =
            res.temperatureC !== undefined ? res.temperatureC : null;
          this.card2TempOk = res.tempOk;
          this.card2GpiosOk = res.gpiosOk;
          this.card2GpioDetails = res.gpioDetails || '';
          this.card2VoltagesOk = res.voltagesOk;
          this.card2Pp5000Mv = res.pp5000Mv || 0;
          this.card2HostVbusMv = res.hostVbusMv || 0;
          this.card2ChgVbusMv = res.chgVbusMv || 0;
          this.card2VoltageDetails = res.voltageDetails || '';

          if (res.allPassed) {
            this.replugStatusValue = 'PASSED';
            this.replugStatus.showPassed();
            autoAdvanceToCard('func-card-3', this.captureResetBtn);
          } else {
            this.replugStatusValue = 'FAILED';
            this.replugStatus.showFailed();
            this.dialogService.openFailure(
              'Host Cable 180° Flip Test Failed',
              res.message || 'Host cable flip verification failed.',
              () => this.runHostReplug(),
              () => {
                this.replugPassed = true;
                this.replugStatusValue = 'BYPASSED';
                this.replugStatus.showBypassed();
                autoAdvanceToCard('func-card-3', this.captureResetBtn);
                this.cdr.detectChanges();
              },
            );
          }
          this.cdr.detectChanges();
        },
        error: err => {
          this.replugInProgress = false;
          this.replugStatusValue = 'FAILED';
          this.replugStatus.showFailed();
          this.dialogService.openFailure(
            'Host Re-plug Test Error',
            err,
            () => this.runHostReplug(),
            () => {
              this.replugPassed = true;
              this.replugStatusValue = 'BYPASSED';
              this.replugStatus.showBypassed();
              autoAdvanceToCard('func-card-3', this.captureResetBtn);
              this.cdr.detectChanges();
            },
          );
          this.cdr.detectChanges();
        },
      });
    this.subs.add(sub);
  }

  bypassReplug(): void {
    this.replugPassed = true;
    this.replugStatusValue = 'BYPASSED';
    this.replugStatus.showBypassed();
    autoAdvanceToCard('func-card-3', this.captureResetBtn);
    this.cdr.detectChanges();
  }

  // Card 3: RESET Button Test
  captureResetBaseline(): void {
    this.resetBaselineInProgress = true;
    this.resetStatus.showWorking();
    this.cdr.detectChanges();

    const sub = this.mauiService
      .testResetButton(
        this.serialNumber,
        this.devicePort,
        'check_initial_uptime',
      )
      .subscribe({
        next: res => {
          this.resetBaselineInProgress = false;
          this.resetBaselineCaptured = res.uptimeSufficient;
          this.initialUptimeMs = parseInt(res.currentUptimeMs, 10) || 0;
          this.resetMessage = res.message || '';

          if (res.uptimeSufficient) {
            this.resetStatus.showPassed();
          } else {
            this.resetStatus.showFailed();
            this.dialogService.openFailure(
              'Reset Baseline Capture Failed',
              res.message || 'Initial uptime was not sufficient.',
              () => this.captureResetBaseline(),
              () => {
                this.resetBaselineCaptured = true;
                this.initialUptimeMs = 15000;
                this.resetStatus.showBypassed();
                this.cdr.detectChanges();
              },
            );
          }
          this.cdr.detectChanges();
        },
        error: err => {
          this.resetBaselineInProgress = false;
          this.resetStatus.showFailed();
          this.dialogService.openFailure(
            'Reset Baseline Error',
            err,
            () => this.captureResetBaseline(),
            () => {
              this.resetBaselineCaptured = true;
              this.initialUptimeMs = 15000;
              this.resetStatus.showBypassed();
              this.cdr.detectChanges();
            },
          );
          this.cdr.detectChanges();
        },
      });
    this.subs.add(sub);
  }

  verifyResetReboot(): void {
    this.resetVerifyInProgress = true;
    this.resetStatus.showWorking();
    this.cdr.detectChanges();

    const sub = this.mauiService
      .testResetButton(
        this.serialNumber,
        this.devicePort,
        'verify_reboot',
        this.initialUptimeMs || 10000,
      )
      .subscribe({
        next: res => {
          this.resetVerifyInProgress = false;
          this.resetPassed = res.resetVerified;
          this.resetMessage = res.message || '';

          if (res.resetVerified) {
            this.resetStatusValue = 'PASSED';
            this.resetStatus.showPassed();
            autoAdvanceToCard('func-card-4', this.readBslBtn);
          } else {
            this.resetStatusValue = 'FAILED';
            this.resetStatus.showFailed();
            this.dialogService.openFailure(
              'Reset Button Test Failed',
              res.message ||
                'Reboot was not detected after RESET button press.',
              () => this.verifyResetReboot(),
              () => {
                this.resetPassed = true;
                this.resetStatusValue = 'BYPASSED';
                this.resetStatus.showBypassed();
                autoAdvanceToCard('func-card-4', this.readBslBtn);
                this.cdr.detectChanges();
              },
            );
          }
          this.cdr.detectChanges();
        },
        error: err => {
          this.resetVerifyInProgress = false;
          this.resetStatusValue = 'FAILED';
          this.resetStatus.showFailed();
          this.dialogService.openFailure(
            'Reset Button Test Error',
            err,
            () => this.verifyResetReboot(),
            () => {
              this.resetPassed = true;
              this.resetStatusValue = 'BYPASSED';
              this.resetStatus.showBypassed();
              autoAdvanceToCard('func-card-4', this.readBslBtn);
              this.cdr.detectChanges();
            },
          );
          this.cdr.detectChanges();
        },
      });
    this.subs.add(sub);
  }

  bypassReset(): void {
    this.resetPassed = true;
    this.resetStatusValue = 'BYPASSED';
    this.resetStatus.showBypassed();
    autoAdvanceToCard('func-card-4', this.readBslBtn);
    this.cdr.detectChanges();
  }

  // Card 4: BSL Button Test
  readBslButton(): void {
    if (this.rev.bslMethod === 'gpio') {
      this.runGuidedBslCheck();
      return;
    }
    this.bslAdcInProgress = true;
    this.bslStatus.showWorking();
    this.cdr.detectChanges();

    const sub = this.mauiService
      .testBslButton(this.serialNumber, this.devicePort)
      .subscribe({
        next: res => {
          this.bslAdcInProgress = false;
          this.peakBslMv = res.peakBslMv;
          this.onBslResult(
            res.success,
            res.message || 'BSL ADC voltage did not rise above threshold.',
          );
        },
        error: err => this.onBslError(err),
      });
    this.subs.add(sub);
  }

  /**
   * v1.2: guided check (not touched -> press and hold -> release) with the
   * live prompt and countdown streamed by the server.
   */
  private runGuidedBslCheck(): void {
    this.bslAdcInProgress = true;
    this.bslMessage = '';
    this.bslFailedPhase = 0;
    this.bslPhase = 1;
    this.bslPrompt = 'Do not touch the BSL button';
    this.bslSecondsLeft = 0;
    this.bslLevel = -1;
    this.bslStatus.showWorking();
    this.cdr.detectChanges();

    let final: MauiBslButtonProgress | null = null;
    const sub = this.mauiService
      .streamBslButtonTest(this.serialNumber, this.devicePort)
      .subscribe({
        next: p => {
          this.bslPhase = p.phase;
          this.bslPrompt = p.prompt;
          this.bslSecondsLeft = p.secondsLeft;
          this.bslLevel = p.level;
          if (p.done) {
            final = p;
          }
          this.cdr.detectChanges();
        },
        error: err => {
          this.bslPhase = 0;
          this.onBslError(err);
        },
        complete: () => {
          this.bslAdcInProgress = false;
          this.bslPhase = 0;
          const result = final as MauiBslButtonProgress | null;
          this.peakBslMv = Math.max(result?.level ?? 0, 0);
          this.bslFailedPhase = result?.success ? 0 : result?.failedPhase || 0;
          this.onBslResult(
            !!result?.success,
            result?.message ||
              'BSL check ended without a result (MCU console lost?).',
          );
        },
      });
    this.subs.add(sub);
  }

  private onBslResult(success: boolean, message: string): void {
    this.bslAdcInProgress = false;
    this.bslPassed = success;
    this.bslMessage = message;
    if (success) {
      this.bslStatusValue = 'PASSED';
      this.bslStatus.showPassed();
      autoAdvanceToCard('func-card-5', this.confirmLedsBtn);
    } else {
      this.bslStatusValue = 'FAILED';
      this.bslStatus.showFailed();
      this.dialogService.openFailure(
        'BSL Button Test Failed',
        message,
        () => this.readBslButton(),
        () => this.bypassBsl(),
      );
    }
    this.cdr.detectChanges();
  }

  private onBslError(err: unknown): void {
    this.bslAdcInProgress = false;
    this.bslStatusValue = 'FAILED';
    this.bslStatus.showFailed();
    this.dialogService.openFailure(
      'BSL Button Test Error',
      err,
      () => this.readBslButton(),
      () => this.bypassBsl(),
    );
    this.cdr.detectChanges();
  }

  bypassBsl(): void {
    this.bslPassed = true;
    this.bslStatusValue = 'BYPASSED';
    this.bslStatus.showBypassed();
    autoAdvanceToCard('func-card-5', this.confirmLedsBtn);
    this.cdr.detectChanges();
  }

  // Card 5: Visual LED Check
  confirmLeds(): void {
    if (this.ledAmberChecked && this.ledGreenChecked) {
      this.ledPassed = true;
      this.ledStatusValue = 'PASSED';
      this.ledStatus.showPassed();
      autoAdvanceToCard('func-card-6', this.stage1Btn);
    } else {
      this.ledPassed = false;
      this.ledStatusValue = 'FAILED';
      this.ledStatus.showFailed();
      this.dialogService.openFailure(
        'LED Verification Incomplete',
        this.rev.leds.incompleteMessage,
        () => {},
        () => {
          this.ledAmberChecked = true;
          this.ledGreenChecked = true;
          this.ledPassed = true;
          this.ledStatusValue = 'BYPASSED';
          this.ledStatus.showBypassed();
          autoAdvanceToCard('func-card-6', this.stage1Btn);
          this.cdr.detectChanges();
        },
      );
    }
    this.cdr.detectChanges();
  }

  bypassLeds(): void {
    this.ledAmberChecked = true;
    this.ledGreenChecked = true;
    this.ledPassed = true;
    this.ledStatusValue = 'BYPASSED';
    this.ledStatus.showBypassed();
    autoAdvanceToCard('func-card-6', this.stage1Btn);
    this.cdr.detectChanges();
  }

  // Card 6: Host + DUT + Charger Verification
  runStage1(): void {
    this.stage1InProgress = true;
    this.stage1Status.showWorking();
    this.cdr.detectChanges();

    const sub = timer(this.stabilizationDelayMs)
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
          this.stage1Passed = res.allPassed;
          this.stage1Orientation = res.dutOrientation || '';
          this.h2hLinkSpeed = res.h2hSpeed5gbps
            ? '5 Gbps (SuperSpeed)'
            : 'Non-SuperSpeed';
          this.h2hSerialMatched = res.h2hSerialMatched;
          this.adbConnected = res.adbConnected;
          this.adbDutSpeed = res.adbDutSpeed || '';
          this.apUartOk = res.apUartOk;
          this.ecUartOk = res.ecUartOk;
          this.gscUartOk = res.gscUartOk;
          this.apVersionStr = res.apVersionStr || '';
          this.ecVersionStr = res.ecVersionStr || '';
          this.gscVersionStr = res.gscVersionStr || '';
          this.negotiatedVoltage = res.negotiatedVoltageV || 0;
          this.negotiatedCurrent = res.negotiatedCurrentA || 0;
          this.measuredVbusMv = res.measuredVbusMv || 0;
          this.calculatedPowerW =
            res.calculatedPowerW ||
            Math.round(
              (res.negotiatedVoltageV || 0) * (res.negotiatedCurrentA || 0),
            );

          if (res.allPassed) {
            this.stage1StatusValue = 'PASSED';
            this.stage1Status.showPassed();
            autoAdvanceToCard('func-card-7', this.stage2Btn);
          } else {
            this.stage1StatusValue = 'FAILED';
            this.stage1Status.showFailed();
            this.dialogService.openFailure(
              'Host + DUT + Charger Test Failed',
              res.summary || 'Host+DUT verification failed.',
              () => this.runStage1(),
              () => {
                this.stage1Passed = true;
                this.stage1StatusValue = 'BYPASSED';
                this.stage1Status.showBypassed();
                autoAdvanceToCard('func-card-7', this.stage2Btn);
                this.cdr.detectChanges();
              },
            );
          }
          this.cdr.detectChanges();
        },
        error: err => {
          this.stage1InProgress = false;
          this.stage1StatusValue = 'FAILED';
          this.stage1Status.showFailed();
          this.dialogService.openFailure(
            'Host + DUT Test Error',
            err,
            () => this.runStage1(),
            () => {
              this.stage1Passed = true;
              this.stage1StatusValue = 'BYPASSED';
              this.stage1Status.showBypassed();
              autoAdvanceToCard('func-card-7', this.stage2Btn);
              this.cdr.detectChanges();
            },
          );
          this.cdr.detectChanges();
        },
      });
    this.subs.add(sub);
  }

  bypassStage1(): void {
    this.stage1Passed = true;
    this.stage1StatusValue = 'BYPASSED';
    this.stage1Status.showBypassed();
    autoAdvanceToCard('func-card-7', this.stage2Btn);
    this.cdr.detectChanges();
  }

  // Card 7: 180° SBU Cable Flip Test
  runStage2(): void {
    this.stage2InProgress = true;
    this.stage2Status.showWorking();
    this.cdr.detectChanges();

    const sub = timer(this.stabilizationDelayMs)
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
          this.dutOrientation = res.dutOrientation || '';

          if (res.allPassed) {
            this.stage2StatusValue = 'PASSED';
            this.stage2Status.showPassed();
            autoAdvanceToCard('func-card-adb', this.adbTransferBtn);
          } else {
            this.stage2StatusValue = 'FAILED';
            this.stage2Status.showFailed();
            this.dialogService.openFailure(
              '180° SBU Cable Flip Test Failed',
              res.summary || 'SBU flip polarity verification failed.',
              () => this.runStage2(),
              () => {
                this.stage2Passed = true;
                this.stage2StatusValue = 'BYPASSED';
                this.stage2Status.showBypassed();
                autoAdvanceToCard('func-card-adb', this.adbTransferBtn);
                this.cdr.detectChanges();
              },
            );
          }
          this.cdr.detectChanges();
        },
        error: err => {
          this.stage2InProgress = false;
          this.stage2StatusValue = 'FAILED';
          this.stage2Status.showFailed();
          this.dialogService.openFailure(
            '180° Flip Test Error',
            err,
            () => this.runStage2(),
            () => {
              this.stage2Passed = true;
              this.stage2StatusValue = 'BYPASSED';
              this.stage2Status.showBypassed();
              autoAdvanceToCard('func-card-adb', this.adbTransferBtn);
              this.cdr.detectChanges();
            },
          );
          this.cdr.detectChanges();
        },
      });
    this.subs.add(sub);
  }

  bypassStage2(): void {
    this.stage2Passed = true;
    this.stage2StatusValue = 'BYPASSED';
    this.stage2Status.showBypassed();
    autoAdvanceToCard('func-card-adb', this.adbTransferBtn);
    this.cdr.detectChanges();
  }

  // Card 9: Charger Disconnect Test (after the ADB transfer)
  runStage3(): void {
    this.stage3InProgress = true;
    this.stage3Status.showWorking();
    this.cdr.detectChanges();

    const sub = timer(this.stabilizationDelayMs)
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
          this.stage3Passed = res.allPassed;
          this.chargerDisconnected = res.chargerDisconnectedOk;

          if (res.allPassed) {
            this.stage3StatusValue = 'PASSED';
            this.stage3Status.showPassed();
            this.advanceToLogs();
          } else {
            this.stage3StatusValue = 'FAILED';
            this.stage3Status.showFailed();
            this.dialogService.openFailure(
              'Charger Disconnect Test Failed',
              res.summary || 'Charger disconnection check failed.',
              () => this.runStage3(),
              () => {
                this.stage3Passed = true;
                this.stage3StatusValue = 'BYPASSED';
                this.stage3Status.showBypassed();
                this.advanceToLogs();
                this.cdr.detectChanges();
              },
            );
          }
          this.cdr.detectChanges();
        },
        error: err => {
          this.stage3InProgress = false;
          this.stage3StatusValue = 'FAILED';
          this.stage3Status.showFailed();
          this.dialogService.openFailure(
            'Charger Disconnect Error',
            err,
            () => this.runStage3(),
            () => {
              this.stage3Passed = true;
              this.stage3StatusValue = 'BYPASSED';
              this.stage3Status.showBypassed();
              this.advanceToLogs();
              this.cdr.detectChanges();
            },
          );
          this.cdr.detectChanges();
        },
      });
    this.subs.add(sub);
  }

  bypassStage3(): void {
    this.stage3Passed = true;
    this.stage3StatusValue = 'BYPASSED';
    this.stage3Status.showBypassed();
    this.advanceToLogs();
    this.cdr.detectChanges();
  }

  // Card 8: ADB Data Transfer Reliability (after the SBU flip card)
  runAdbTransfer(): void {
    this.adbInProgress = true;
    this.adbTransfer = null;
    this.adbStatus.showWorking();
    this.cdr.detectChanges();

    const sub = this.mauiService
      .runAdbTransferTest(this.serialNumber)
      .subscribe({
        next: res => {
          this.adbInProgress = false;
          this.adbTransfer = res;
          this.adbPassed = res.success;
          if (res.success) {
            this.adbStatusValue = 'PASSED';
            this.adbStatus.showPassed();
            autoAdvanceToCard('func-card-charger', this.stage3Btn);
          } else {
            this.adbStatusValue = 'FAILED';
            this.adbStatus.showFailed();
            this.dialogService.openFailure(
              'ADB Transfer Test Failed',
              res.message || 'ADB data transfer check failed.',
              () => this.runAdbTransfer(),
              () => this.bypassAdbTransfer(),
            );
          }
          this.cdr.detectChanges();
        },
        error: err => {
          this.adbInProgress = false;
          this.adbStatusValue = 'FAILED';
          this.adbStatus.showFailed();
          this.dialogService.openFailure(
            'ADB Transfer Test Error',
            err,
            () => this.runAdbTransfer(),
            () => this.bypassAdbTransfer(),
          );
          this.cdr.detectChanges();
        },
      });
    this.subs.add(sub);
  }

  bypassAdbTransfer(): void {
    this.adbPassed = true;
    this.adbStatusValue = 'BYPASSED';
    this.adbStatus.showBypassed();
    this.logBypass(
      'adb_transfer_test',
      'ADB Data Transfer Reliability',
      this.adbTransfer?.message ?? '',
    );
    autoAdvanceToCard('func-card-charger', this.stage3Btn);
    this.cdr.detectChanges();
  }

  /**
   * Records a bypassed card in its stage log so that the log checklist and
   * the final verdict show BYPASSED instead of the last failure (or
   * NOT_RUN). |done| runs once the log was written (or failed to).
   */
  private logBypass(
    logStepName: string,
    stageLabel: string,
    lastResult: string,
    done?: () => void,
  ): void {
    const serial = this.serialNumber || this.lastTestedSerial || 'UNKNOWN';
    const content =
      `=== ${stageLabel} ===\n` +
      `Date: ${new Date().toISOString()}\n` +
      'STATUS=BYPASSED\n' +
      'RESULT: BYPASSED\n' +
      'Bypassed by operator.\n' +
      (lastResult ? `Last result before the bypass: ${lastResult}\n` : '');
    const sub = this.deviceMgmtService
      .saveCardLog(serial, logStepName, content)
      .subscribe({next: () => done?.(), error: () => done?.()});
    this.subs.add(sub);
  }

  private advanceToLogs(): void {
    this.inspectLogs();
    autoAdvanceToCard('func-card-logs', this.saveLogsBtn);
  }

  inspectLogs(): void {
    const targetSerial = this.serialNumber || 'UNKNOWN';
    this.inspectingLogs = true;
    const sub = this.deviceMgmtService
      .getDeviceLogsStatus(targetSerial)
      .subscribe({
        next: res => {
          this.inspectingLogs = false;
          this.logsInspected = true;
          this.logFiles = res.logs || [];
          this.logsDirectory = res.directoryPath || '';
          this.overallLogsVerdict = res.overallVerdict || '';
          this.cdr.detectChanges();
        },
        error: () => {
          this.inspectingLogs = false;
          this.logsInspected = true;
          this.cdr.detectChanges();
        },
      });
    this.subs.add(sub);
  }

  /**
   * Cards of this run that did not pass, as "<card>: <status>". A bypass only
   * unlocks the next card: bypassed cards are listed here and fail the unit.
   */
  get notPassedCards(): string[] {
    const cards: Array<[string, string]> = [];
    if (this.isFatpMode) {
      cards.push([
        'Barcode',
        this.barcodeBypassed
          ? 'BYPASSED'
          : this.barcodeMatches
            ? 'PASSED'
            : 'NOT_RUN',
      ]);
    }
    if (this.rev.requiresSerialProvisioning) {
      cards.push([
        `Card ${this.cardNo(-1)} (Serial Provisioning)`,
        this.serialProvisionStatus,
      ]);
    }
    if (this.rev.requiresH2hSpiCheck) {
      cards.push([
        `Card ${this.cardNo(0)} (H2H SPI Flash Access)`,
        this.h2hSpiStatus,
      ]);
    }
    cards.push(
      [`Card ${this.cardNo(1)} (Host USB & Console)`, this.usbStatusValue],
      [`Card ${this.cardNo(2)} (Host 180° Flip)`, this.replugStatusValue],
      [`Card ${this.cardNo(3)} (RESET Button)`, this.resetStatusValue],
      [`Card ${this.cardNo(4)} (BSL Button)`, this.bslStatusValue],
      [`Card ${this.cardNo(5)} (Visual LEDs)`, this.ledStatusValue],
      [`Card ${this.cardNo(6)} (Host+DUT+Charger)`, this.stage1StatusValue],
      [`Card ${this.cardNo(7)} (180° SBU Flip)`, this.stage2StatusValue],
      [`Card ${this.cardNo(8)} (ADB Transfer)`, this.adbStatusValue],
      [`Card ${this.cardNo(9)} (Charger Disconnected)`, this.stage3StatusValue],
    );
    return cards
      .filter(([, status]) => status !== 'PASSED')
      .map(([card, status]) => `${card}: ${status}`);
  }

  // Card 10: Save Logs & Export (after the charger disconnect card)
  saveLogs(): void {
    if (this.deviceConnected) {
      this.dialogService.openFailure(
        'Unit Still Connected',
        'Please disconnect the Maui unit from the host USB port before saving logs and finalizing the report.',
        () => this.saveLogs(),
      );
      return;
    }

    this.savingLogs = true;
    this.completeStatus.showWorking();

    const targetSerial =
      this.lastTestedSerial || this.serialNumber || 'UNKNOWN';
    const phaseTag = this.isFatpMode ? 'FATP_QT0' : 'SMT_FUNCTIONAL';
    const notPassed = this.notPassedCards;
    const allPassed = notPassed.length === 0;
    this.finalPassed = allPassed;
    this.finalNotPassedCards = notPassed;

    const adbDetails = this.adbTransfer
      ? this.adbSummary(this.adbTransfer)
      : 'no data';

    const summaryContent =
      `=== Maui ${phaseTag} Verification Summary ===\n` +
      `Date: ${new Date().toISOString()}\n` +
      `Serial: ${targetSerial}\n` +
      `Mode: ${this.mode}\n` +
      (this.isFatpMode
        ? `Outer Barcode Scanned: ${this.scannedBarcode}\n`
        : '') +
      (this.rev.requiresSerialProvisioning
        ? `Card ${this.cardNo(-1)} (Serial Provisioning): ${this.serialProvisionStatus}\n`
        : '') +
      (this.rev.requiresH2hSpiCheck
        ? `Card ${this.cardNo(0)} (H2H SPI Flash Access): ${this.h2hSpiStatus}` +
          (this.h2hSpiSerial ? ` (SPI serial ${this.h2hSpiSerial})` : '') +
          '\n'
        : '') +
      `Card ${this.cardNo(1)} (Host USB & Console): ${this.usbStatusValue}\n` +
      `  MCU FW: ${this.mcuVersionStr}, PDC AppConfig: ${this.pdcAppConfigStr}\n` +
      `  HW Revision: ${this.card1Hwrev ?? 'N/A'} (${this.card1HwrevOk ? 'PASS' : 'FAIL'}), Temp: ${this.card1TempC?.toFixed(1) ?? 'N/A'}°C (${this.card1TempOk ? 'PASS' : 'FAIL'})\n` +
      `  GPIO Verification: ${this.card1GpiosOk ? 'PASS' : 'FAIL (' + this.card1GpioDetails + ')'}\n` +
      `  Voltage Verification: ${this.card1VoltagesOk ? 'PASS' : 'FAIL (' + this.card1VoltageDetails + ')'}\n` +
      `Card ${this.cardNo(2)} (Host 180° Flip): ${this.replugStatusValue} (${this.card1Orientation} -> ${this.card2Orientation})\n` +
      `  HW Revision: ${this.card2Hwrev ?? 'N/A'} (${this.card2HwrevOk ? 'PASS' : 'FAIL'}), Temp: ${this.card2TempC?.toFixed(1) ?? 'N/A'}°C (${this.card2TempOk ? 'PASS' : 'FAIL'})\n` +
      `  GPIO Verification: ${this.card2GpiosOk ? 'PASS' : 'FAIL (' + this.card2GpioDetails + ')'}\n` +
      `  Voltage Verification: ${this.card2VoltagesOk ? 'PASS' : 'FAIL (' + this.card2VoltageDetails + ')'}\n` +
      `Card ${this.cardNo(3)} (RESET Button): ${this.resetStatusValue}\n` +
      (this.rev.bslMethod === 'gpio'
        ? `Card ${this.cardNo(4)} (BSL Button GPIO, released -> pressed and held -> released): ${this.bslStatusValue}` +
          (this.bslFailedPhase
            ? ` (failed at step ${this.bslFailedPhase})`
            : '') +
          '\n'
        : `Card ${this.cardNo(4)} (BSL Button ADC): ${this.bslStatusValue} (Peak BSL: ${this.peakBslMv} mV)\n`) +
      `Card ${this.cardNo(5)} (Visual LEDs): ${this.ledStatusValue} (Amber=${this.ledAmberChecked}, Green_1Hz=${this.ledGreenChecked})\n` +
      `Card ${this.cardNo(6)} (Host+DUT+Charger): ${this.stage1StatusValue} (H2H=${this.h2hLinkSpeed}, PD=${this.negotiatedVoltage}V/${this.calculatedPowerW}W, CCD EC/GSC=${this.ecUartOk && this.gscUartOk})\n` +
      `Card ${this.cardNo(7)} (180° SBU Flip): ${this.stage2StatusValue} (DUT SBU Orientation=${this.dutOrientation})\n` +
      `Card ${this.cardNo(8)} (ADB Transfer, two ~400 MB random files): ${this.adbStatusValue} (${adbDetails})\n` +
      `Card ${this.cardNo(9)} (Charger Disconnected): ${this.stage3StatusValue} (Disconnected=${this.chargerDisconnected})\n` +
      (allPassed ? '' : `Not passed: ${notPassed.join(', ')}\n`) +
      `OVERALL RESULT: ${allPassed ? 'PASS' : 'FAIL'}\n`;

    const logFileName = this.isFatpMode
      ? 'fatp_qt0_summary'
      : 'smt_functional_summary';

    const sub = this.deviceMgmtService
      .saveCardLog(targetSerial, logFileName, summaryContent)
      .subscribe({
        next: () => {
          const submitSub = this.deviceMgmtService
            .submitFinalManufacturingResults(
              targetSerial,
              'OP_FACTORY',
              this.isFatpMode ? 'STATION_FATP_QT0' : 'STATION_SMT_FUNCTIONAL',
              true,
              true,
              true,
              this.usbCheckPassed && this.replugPassed && this.resetPassed,
              this.stage1Passed && this.stage2Passed && this.stage3Passed,
              this.isFatpMode && this.barcodeMatches,
              undefined,
              this.adbSerial,
              this.ftdiSerial,
              this.isFatpMode ? this.scannedBarcode : '',
            )
            .subscribe({
              next: res => {
                this.finalMesTrackingId = res.mesTrackingId || '';
                this.finalSummaryPath = res.summaryFilePath || '';
                this.finishRun();
              },
              error: () => this.finishRun(),
            });
          this.subs.add(submitSub);
        },
        error: () => this.finishRun(),
      });
    this.subs.add(sub);
  }

  /**
   * Shows the verdict of the saved run. A passed unit reloads the page for
   * the next unit after 2.5 s; a failed unit waits for "Next Unit" so that
   * the operator sees the failure and puts the unit aside.
   */
  private finishRun(): void {
    this.savingLogs = false;
    this.logsSaved = true;
    if (this.finalPassed) {
      this.completeStatus.showPassed();
    } else {
      this.completeStatus.showFailed();
    }
    this.inspectLogs();
    this.cdr.detectChanges();
    if (this.finalPassed) {
      this.subs.add(timer(2500).subscribe(() => this.reloadForNextUnit()));
    }
  }

  /** "Next Unit" on the final card of a failed unit. */
  nextUnit(): void {
    this.reloadForNextUnit();
  }

  /** Reloads the page for the next unit (a method so that specs can stub it). */
  reloadForNextUnit(): void {
    window.location.reload();
  }

  formatLogSize(sizeBytes?: string | number): string {
    if (!sizeBytes) {
      return '';
    }
    const bytes = Number(sizeBytes);
    if (isNaN(bytes) || bytes <= 0) {
      return '';
    }
    return `${(bytes / 1024).toFixed(1)} KB`;
  }
}
