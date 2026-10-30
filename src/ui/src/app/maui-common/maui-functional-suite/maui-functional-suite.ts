// Copyright 2026 The ChromiumOS Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

import {
  ChangeDetectorRef,
  Component,
  DestroyRef,
  ElementRef,
  HostListener,
  Input,
  OnInit,
  ViewChild,
  inject,
} from '@angular/core';
import {takeUntilDestroyed} from '@angular/core/rxjs-interop';
import {CommonModule} from '@angular/common';
import {FormsModule} from '@angular/forms';
import {MatButtonModule} from '@angular/material/button';
import {MatCheckboxModule} from '@angular/material/checkbox';
import {MatFormFieldModule} from '@angular/material/form-field';
import {MatIconModule} from '@angular/material/icon';
import {MatInputModule} from '@angular/material/input';
import {RecursivePartial} from '@ngx-grpc/common';
import {
  EMPTY,
  Observable,
  catchError,
  exhaustMap,
  switchMap,
  timer,
} from 'rxjs';
import {DiagnosemeReworkCard} from '../../diagnoseme-rework-card/diagnoseme-rework-card';
import {DiagnosemeReworkCardColumn} from '../../diagnoseme-rework-card-column/diagnoseme-rework-card-column';
import {MauiDialogService} from '../maui-dialog.service';
import {autoAdvanceToCard} from '../maui-scroll.utils';
import {
  BARCODE_MIN_LENGTH,
  BARCODE_SCANNER_KEY_GAP_MS,
  BSL_THRESHOLD_MV,
  DEVICE_POLL_INTERVAL_MS,
  EXPECTED_GPIO_COUNT,
  EXPECTED_HWREV,
  MAUI_OPERATOR_ID,
  MAUI_RESULTS_DIR,
  MAUI_STATION_ID,
  MauiStepStatus,
  PAGE_RELOAD_DELAY_MS,
  RESET_BYPASS_BASELINE_UPTIME_MS,
  RESET_DEFAULT_BASELINE_UPTIME_MS,
  STABILIZATION_DELAY_MS,
  TEMPERATURE_RANGE_LABEL,
} from '../maui-constants';
import {requireSuccess} from '../maui-rx.utils';
import {PageReloadService} from '../page-reload.service';
import {MauiStation2Service} from '../../services/maui-station2.service';
import {MauiDeviceMgmtService} from '../../services/maui-device-mgmt.service';
import {TestPassFailButton} from '../../test-pass-fail-button/test-pass-fail-button';
import {
  MauiAdbTransferTestResponse,
  MauiFinalSubmissionRequest,
  MauiHostOnlyTestResponse,
  MauiHostReplugTestResponse,
  MauiLogFileInfo,
  MauiReadSerialResponse,
} from '../../../proto/maui-manufacturing.pb';

/** Test cards of the functional suite, in operator order. */
export type FunctionalCardId =
  | 'barcode'
  | 'hostUsb'
  | 'hostFlip'
  | 'reset'
  | 'bsl'
  | 'leds'
  | 'hostDutCharger'
  | 'dutSbuFlip'
  | 'chargerDisconnect'
  | 'adbTransfer';

export const FUNCTIONAL_CARD_IDS: readonly FunctionalCardId[] = [
  'barcode',
  'hostUsb',
  'hostFlip',
  'reset',
  'bsl',
  'leds',
  'hostDutCharger',
  'dutSbuFlip',
  'chargerDisconnect',
  'adbTransfer',
];

/** Keys used for the per-card results in MES `stage_statuses`. */
export const FUNCTIONAL_STAGE_STATUS_KEYS: Record<FunctionalCardId, string> = {
  barcode: 'outer_barcode_verification',
  hostUsb: 'host_usb_console_check',
  hostFlip: 'host_cable_flip',
  reset: 'reset_button',
  bsl: 'bsl_button',
  leds: 'visual_leds',
  hostDutCharger: 'host_dut_charger',
  dutSbuFlip: 'dut_sbu_flip',
  chargerDisconnect: 'charger_disconnect',
  // Same key as the backend's adb_transfer_test stage log.
  adbTransfer: 'adb_transfer_test',
};

export interface FunctionalCardState {
  inProgress: boolean;
  /** The card is finished (passed or bypassed); unlocks the next card. */
  done: boolean;
  /** The real result, used for the summary and the MES submission. */
  status: MauiStepStatus;
}

/** Host-side health readings shared by Card 1 (USB check) and Card 2 (flip). */
export interface HostCheckResult {
  orientation: string;
  hwrev: number | null;
  hwrevOk: boolean;
  tempC: number | null;
  tempOk: boolean;
  gpiosOk: boolean;
  gpioDetails: string;
  voltagesOk: boolean;
  pp5000Mv: number;
  hostVbusMv: number;
  chgVbusMv: number;
  voltageDetails: string;
}

export function emptyHostCheckResult(): HostCheckResult {
  return {
    orientation: '',
    hwrev: null,
    hwrevOk: false,
    tempC: null,
    tempOk: false,
    gpiosOk: false,
    gpioDetails: '',
    voltagesOk: false,
    pp5000Mv: 0,
    hostVbusMv: 0,
    chgVbusMv: 0,
    voltageDetails: '',
  };
}

export function toHostCheckResult(
  res: MauiHostOnlyTestResponse | MauiHostReplugTestResponse,
): HostCheckResult {
  return {
    orientation: res.hostOrientation || '',
    hwrev: res.detectedHwrev !== undefined ? res.detectedHwrev : null,
    hwrevOk: res.hwrevOk,
    tempC: res.temperatureC !== undefined ? res.temperatureC : null,
    tempOk: res.tempOk,
    gpiosOk: res.gpiosOk,
    gpioDetails: res.gpioDetails || '',
    voltagesOk: res.voltagesOk,
    pp5000Mv: res.pp5000Mv || 0,
    hostVbusMv: res.hostVbusMv || 0,
    chgVbusMv: res.chgVbusMv || 0,
    voltageDetails: res.voltageDetails || '',
  };
}

function createCardStates(): Record<FunctionalCardId, FunctionalCardState> {
  const states = {} as Record<FunctionalCardId, FunctionalCardState>;
  for (const id of FUNCTIONAL_CARD_IDS) {
    states[id] = {inProgress: false, done: false, status: 'NOT_RUN'};
  }
  return states;
}

/** How a test card RPC is run and judged. */
interface CardRunSpec<T> {
  isPassed: (res: T) => boolean;
  /** Stores the readings of a response (called for passing and failing results). */
  onResult?: (res: T) => void;
  /** Called when the result fails or the RPC errors. */
  onFailure?: () => void;
  failureTitle: string;
  failureMessage: (res: T) => string;
  errorTitle: string;
  retry: () => void;
  /** Wait this long before sending the RPC (cable settling). */
  delayMs?: number;
}

/** Where the operator goes after a card is done. */
interface CardFlow {
  statusButton: () => TestPassFailButton | undefined;
  nextCardElementId: string;
  nextFocus: () => ElementRef<HTMLButtonElement> | undefined;
  /** Runs when the card is done (passed or bypassed), before advancing. */
  onDone?: () => void;
  /** Extra state changes when the operator bypasses the card. */
  onBypass?: () => void;
}

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
    TestPassFailButton,
  ],
})
export class MauiFunctionalSuiteComponent implements OnInit {
  @Input() mode: 'smt' | 'fatp_qt0' = 'smt';
  @Input() suiteTitle = 'Maui Functional Verification';
  @Input() suiteSubtitle =
    'System-level functional testing for Maui hardware verification.';

  private readonly mauiService = inject(MauiStation2Service);
  private readonly deviceMgmtService = inject(MauiDeviceMgmtService);
  private readonly dialogService = inject(MauiDialogService);
  private readonly pageReload = inject(PageReloadService);
  private readonly cdr = inject(ChangeDetectorRef);
  private readonly destroyRef = inject(DestroyRef);

  // Display-only expectations used by the template.
  readonly expectedHwrev = EXPECTED_HWREV;
  readonly temperatureRangeLabel = TEMPERATURE_RANGE_LABEL;
  readonly expectedGpioCount = EXPECTED_GPIO_COUNT;
  readonly bslThresholdMv = BSL_THRESHOLD_MV;
  readonly reloadDelaySec = PAGE_RELOAD_DELAY_MS / 1000;
  readonly resultsDir = MAUI_RESULTS_DIR;

  serialNumber = '';
  lastTestedSerial = '';
  devicePort = '';
  deviceConnected = false;
  deviceProgrammed = false;
  ftdiPid = '';
  statusMessage = 'Scanning USB bus for connected Maui device...';

  /**
   * Serial of the unit under test. It is locked once the sequence starts so
   * that all cards and the final report belong to the same unit.
   */
  lockedSerial = '';
  /** Serial of a different unit that showed up after the lock. */
  unexpectedSerial = '';

  // Stabilization delay between operator button click and actual test execution
  stabilizationDelayMs = STABILIZATION_DELAY_MS;

  cards = createCardStates();

  // Barcode scanner buffer (for FATP QT0 mode)
  private barcodeBuffer = '';
  private lastBarcodeKeyTime = 0;
  scannedBarcode = '';
  barcodeCheckFailed = false;
  barcodeMessage = '';
  adbSerial = '';
  ftdiSerial = '';
  dualEndpointsMatch = false;

  // Card 1: Host USB & Console Check
  usbSummary = '';
  mcuVersionStr = '';
  pdcAppConfigStr = '';
  hostUsbCheck = emptyHostCheckResult();

  // Card 2: Host Cable 180° Flip Test
  replugMessage = '';
  hostFlipCheck = emptyHostCheckResult();

  // Card 3: RESET Button Test
  resetBaselineInProgress = false;
  resetBaselineCaptured = false;
  initialUptimeMs = 0;
  resetMessage = '';

  // Card 4: BSL Button Test (ADC Status)
  peakBslMv = 0;
  bslMessage = '';

  // Card 5: Visual LED Check
  ledAmberChecked = false;
  ledGreenChecked = false;

  // Card 6: Host + DUT + Charger Verification (PD & Data Path)
  hostDutOrientation = '';
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
  dutOrientation = '';

  // Card 8: Charger Disconnect Verification (No Charger)
  chargerDisconnected = false;

  // Card 9: ADB Data Transfer Reliability (DUT through the H2H bridge)
  adbTransferResult: MauiAdbTransferTestResponse | null = null;

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
  /** Where the backend archived the results (MauiFinalSubmissionResponse.message). */
  finalResultsMessage = '';

  @ViewChild('barcodeStatus') barcodeStatus?: TestPassFailButton;
  @ViewChild('usbCheckStatus') usbCheckStatus?: TestPassFailButton;
  @ViewChild('replugStatus') replugStatus?: TestPassFailButton;
  @ViewChild('resetStatus') resetStatus?: TestPassFailButton;
  @ViewChild('bslStatus') bslStatus?: TestPassFailButton;
  @ViewChild('ledStatus') ledStatus?: TestPassFailButton;
  @ViewChild('hostDutChargerStatus') hostDutChargerStatus?: TestPassFailButton;
  @ViewChild('dutSbuFlipStatus') dutSbuFlipStatus?: TestPassFailButton;
  @ViewChild('chargerDisconnectStatus')
  chargerDisconnectStatus?: TestPassFailButton;
  @ViewChild('adbTransferStatus') adbTransferStatus?: TestPassFailButton;
  @ViewChild('completeStatus') completeStatus?: TestPassFailButton;

  @ViewChild('usbCheckBtn', {read: ElementRef})
  usbCheckBtn?: ElementRef<HTMLButtonElement>;
  @ViewChild('replugBtn', {read: ElementRef})
  replugBtn?: ElementRef<HTMLButtonElement>;
  @ViewChild('captureResetBtn', {read: ElementRef})
  captureResetBtn?: ElementRef<HTMLButtonElement>;
  @ViewChild('readBslBtn', {read: ElementRef})
  readBslBtn?: ElementRef<HTMLButtonElement>;
  @ViewChild('confirmLedsBtn', {read: ElementRef})
  confirmLedsBtn?: ElementRef<HTMLButtonElement>;
  @ViewChild('hostDutChargerBtn', {read: ElementRef})
  hostDutChargerBtn?: ElementRef<HTMLButtonElement>;
  @ViewChild('dutSbuFlipBtn', {read: ElementRef})
  dutSbuFlipBtn?: ElementRef<HTMLButtonElement>;
  @ViewChild('chargerDisconnectBtn', {read: ElementRef})
  chargerDisconnectBtn?: ElementRef<HTMLButtonElement>;
  @ViewChild('adbTransferBtn', {read: ElementRef})
  adbTransferBtn?: ElementRef<HTMLButtonElement>;
  @ViewChild('saveLogsBtn', {read: ElementRef})
  saveLogsBtn?: ElementRef<HTMLButtonElement>;

  private readonly cardFlow: Record<FunctionalCardId, CardFlow> = {
    barcode: {
      statusButton: () => this.barcodeStatus,
      nextCardElementId: 'func-card-1',
      nextFocus: () => this.usbCheckBtn,
      onBypass: () => {
        this.barcodeMessage = 'Barcode verification bypassed by operator.';
      },
    },
    hostUsb: {
      statusButton: () => this.usbCheckStatus,
      nextCardElementId: 'func-card-2',
      nextFocus: () => this.replugBtn,
    },
    hostFlip: {
      statusButton: () => this.replugStatus,
      nextCardElementId: 'func-card-3',
      nextFocus: () => this.captureResetBtn,
    },
    reset: {
      statusButton: () => this.resetStatus,
      nextCardElementId: 'func-card-4',
      nextFocus: () => this.readBslBtn,
    },
    bsl: {
      statusButton: () => this.bslStatus,
      nextCardElementId: 'func-card-5',
      nextFocus: () => this.confirmLedsBtn,
    },
    leds: {
      statusButton: () => this.ledStatus,
      nextCardElementId: 'func-card-6',
      nextFocus: () => this.hostDutChargerBtn,
      onBypass: () => {
        this.ledAmberChecked = true;
        this.ledGreenChecked = true;
      },
    },
    hostDutCharger: {
      statusButton: () => this.hostDutChargerStatus,
      nextCardElementId: 'func-card-7',
      nextFocus: () => this.dutSbuFlipBtn,
    },
    dutSbuFlip: {
      statusButton: () => this.dutSbuFlipStatus,
      nextCardElementId: 'func-card-8',
      nextFocus: () => this.chargerDisconnectBtn,
    },
    chargerDisconnect: {
      statusButton: () => this.chargerDisconnectStatus,
      nextCardElementId: 'func-card-adb',
      nextFocus: () => this.adbTransferBtn,
    },
    adbTransfer: {
      statusButton: () => this.adbTransferStatus,
      nextCardElementId: 'func-card-9',
      nextFocus: () => this.saveLogsBtn,
      onDone: () => this.inspectLogs(),
    },
  };

  get isFatpMode(): boolean {
    return this.mode === 'fatp_qt0';
  }

  /** Path of the archived report returned by the backend (default dir as fallback). */
  get archiveLocation(): string {
    return this.finalSummaryPath || `${this.resultsDir}/`;
  }

  /** The outer barcode matched (or was bypassed); unlocks Card 1. */
  get barcodeMatches(): boolean {
    return this.cards.barcode.done;
  }

  get canSaveLogs(): boolean {
    return (
      this.cards.adbTransfer.done &&
      !this.deviceConnected &&
      !this.otherUnitDetected &&
      !this.savingLogs &&
      !this.logsSaved
    );
  }

  /**
   * A unit other than the locked unit under test is connected. All run,
   * bypass and save actions are blocked until the locked unit is back.
   */
  get otherUnitDetected(): boolean {
    return !!this.unexpectedSerial;
  }

  ngOnInit(): void {
    this.pollDeviceDetection();
  }

  @HostListener('window:keydown', ['$event'])
  handleKeyboardEvent(event: KeyboardEvent): void {
    if (!this.isFatpMode || this.barcodeMatches) {
      return;
    }
    const currentTime = Date.now();
    if (currentTime - this.lastBarcodeKeyTime > BARCODE_SCANNER_KEY_GAP_MS) {
      this.barcodeBuffer = '';
    }
    this.lastBarcodeKeyTime = currentTime;

    if (event.key === 'Enter') {
      if (this.barcodeBuffer.trim().length >= BARCODE_MIN_LENGTH) {
        this.scannedBarcode = this.barcodeBuffer.trim();
        this.barcodeBuffer = '';
        this.verifyBarcode();
        event.preventDefault();
      }
    } else if (event.key.length === 1) {
      this.barcodeBuffer += event.key;
    }
  }

  private pollDeviceDetection(): void {
    timer(0, DEVICE_POLL_INTERVAL_MS)
      .pipe(
        exhaustMap(() => this.readDeviceStatus()),
        takeUntilDestroyed(this.destroyRef),
      )
      .subscribe(res => this.applyDeviceStatus(res));
  }

  refreshDeviceStatus(): void {
    this.readDeviceStatus()
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe(res =>
        this.applyDeviceStatus(res, /* replaceIdentity= */ true),
      );
  }

  /** Reads the connected device; errors are reported and swallowed. */
  private readDeviceStatus(): Observable<MauiReadSerialResponse> {
    return this.deviceMgmtService.readConnectedDeviceSerial().pipe(
      catchError(() => {
        this.statusMessage = 'Device detection error.';
        this.cdr.detectChanges();
        return EMPTY;
      }),
    );
  }

  /**
   * Applies a device detection result. The poll only fills in identity fields
   * that are reported; a manual refresh (`replaceIdentity`) also clears them
   * when the response is empty. Nothing is taken over from a different unit
   * while the sequence is locked to the unit under test.
   */
  private applyDeviceStatus(
    res: MauiReadSerialResponse,
    replaceIdentity = false,
  ): void {
    this.deviceConnected = res.isConnected;
    const isOtherUnit =
      !!this.lockedSerial &&
      res.isConnected &&
      !!res.serialNumber &&
      res.serialNumber !== this.lockedSerial;
    this.unexpectedSerial = isOtherUnit ? res.serialNumber : '';
    if (isOtherUnit) {
      // The banner names the other unit; keep everything else of the locked unit.
      this.cdr.detectChanges();
      return;
    }

    if (replaceIdentity) {
      this.serialNumber = res.serialNumber;
      this.devicePort = res.devicePort;
      this.adbSerial = res.adbSerial;
      this.ftdiSerial = res.ftdiSerial;
      if (res.isConnected && res.serialNumber) {
        this.lastTestedSerial = res.serialNumber;
      }
    } else {
      if (res.isConnected && res.serialNumber) {
        this.serialNumber = res.serialNumber;
        this.lastTestedSerial = res.serialNumber;
      }
      if (res.devicePort) {
        this.devicePort = res.devicePort;
      }
      if (res.adbSerial) {
        this.adbSerial = res.adbSerial;
      }
      if (res.ftdiSerial) {
        this.ftdiSerial = res.ftdiSerial;
      }
    }
    this.dualEndpointsMatch = res.dualEndpointsMatch;
    this.deviceProgrammed = !!res.ftdiIsProgrammed;
    this.ftdiPid = res.ftdiPid ? '0x' + res.ftdiPid.toString(16) : '';

    if (this.deviceConnected) {
      if (this.deviceProgrammed) {
        this.statusMessage = `Connected Maui Device: ${this.serialNumber || this.lastTestedSerial} (${this.devicePort})`;
      } else {
        this.statusMessage = `Unprogrammed Device Detected: ${this.serialNumber || 'Unknown'} (FTDI PID: ${this.ftdiPid}) — Step 1 provisioning required`;
      }
    } else {
      this.statusMessage =
        'No Maui Device Detected. Please connect Maui Host USB-C cable.';
    }
    this.cdr.detectChanges();
  }

  private lockSerial(): void {
    if (!this.lockedSerial && this.serialNumber) {
      this.lockedSerial = this.serialNumber;
    }
  }

  private isPassed(card: FunctionalCardId): boolean {
    return this.cards[card].status === 'PASSED';
  }

  /** Runs a card's RPC and handles pass, failure, error and bypass. */
  private runCard<T>(
    card: FunctionalCardId,
    request: () => Observable<T>,
    spec: CardRunSpec<T>,
  ): void {
    const state = this.cards[card];
    if (this.otherUnitDetected) {
      return;
    }
    if (card !== 'barcode') {
      this.lockSerial();
    }
    state.inProgress = true;
    this.cardFlow[card].statusButton()?.showWorking();
    this.cdr.detectChanges();

    const result$ = spec.delayMs
      ? timer(spec.delayMs).pipe(switchMap(request))
      : request();
    result$.pipe(takeUntilDestroyed(this.destroyRef)).subscribe({
      next: res => {
        state.inProgress = false;
        spec.onResult?.(res);
        if (spec.isPassed(res)) {
          this.markCardPassed(card);
        } else {
          this.markCardFailed(card, /* resetDone= */ true);
          spec.onFailure?.();
          this.openCardFailure(
            card,
            spec.failureTitle,
            spec.failureMessage(res),
            spec.retry,
          );
        }
        this.cdr.detectChanges();
      },
      error: err => {
        state.inProgress = false;
        this.markCardFailed(card, /* resetDone= */ false);
        spec.onFailure?.();
        this.openCardFailure(card, spec.errorTitle, err, spec.retry);
        this.cdr.detectChanges();
      },
    });
  }

  private openCardFailure(
    card: FunctionalCardId,
    title: string,
    error: unknown,
    retry: () => void,
  ): void {
    this.dialogService.openFailure(title, error, retry, () =>
      this.bypassCard(card),
    );
  }

  private markCardPassed(card: FunctionalCardId): void {
    const state = this.cards[card];
    state.status = 'PASSED';
    state.done = true;
    this.cardFlow[card].statusButton()?.showPassed();
    this.advanceFrom(card);
  }

  private markCardFailed(card: FunctionalCardId, resetDone: boolean): void {
    const state = this.cards[card];
    state.status = 'FAILED';
    if (resetDone) {
      state.done = false;
    }
    this.cardFlow[card].statusButton()?.showFailed();
  }

  /**
   * Lets the operator continue past a card. The card stays recorded as
   * BYPASSED (not passed) in the summary and the MES submission.
   */
  bypassCard(card: FunctionalCardId): void {
    if (this.otherUnitDetected) {
      return;
    }
    const state = this.cards[card];
    state.status = 'BYPASSED';
    state.done = true;
    const flow = this.cardFlow[card];
    flow.onBypass?.();
    flow.statusButton()?.showBypassed();
    this.advanceFrom(card);
    this.cdr.detectChanges();
  }

  private advanceFrom(card: FunctionalCardId): void {
    const flow = this.cardFlow[card];
    this.lockSerial();
    flow.onDone?.();
    autoAdvanceToCard(flow.nextCardElementId, flow.nextFocus());
  }

  // Card 0: Barcode Verification (FATP QT0)
  verifyBarcode(): void {
    const barcode = this.scannedBarcode.trim();
    if (!barcode || this.cards.barcode.inProgress) {
      return;
    }
    this.barcodeCheckFailed = false;
    this.runCard(
      'barcode',
      () =>
        this.deviceMgmtService.verifyScannedLabel(
          this.serialNumber,
          barcode,
          this.adbSerial,
          this.ftdiSerial,
        ),
      {
        isPassed: res => res.matches,
        onResult: res => {
          this.barcodeMessage = res.message;
        },
        onFailure: () => {
          this.barcodeCheckFailed = true;
        },
        failureTitle: 'Barcode Serial Mismatch',
        failureMessage: res => res.message,
        errorTitle: 'Barcode Verification Error',
        retry: () => this.verifyBarcode(),
      },
    );
  }

  // Card 1: Host USB & Console Check
  runUsbCheck(): void {
    this.runCard(
      'hostUsb',
      () =>
        this.mauiService.runHostOnlyTests(this.serialNumber, this.devicePort),
      {
        delayMs: this.stabilizationDelayMs,
        isPassed: res => res.allPassed,
        onResult: res => {
          this.usbSummary = res.summary || '';
          this.mcuVersionStr = res.mcuVersionStr || '';
          this.pdcAppConfigStr = res.pdcAppconfigVer || '';
          this.hostUsbCheck = toHostCheckResult(res);
        },
        failureTitle: 'Host USB & Console Check Failed',
        failureMessage: res => res.summary || 'Host USB or PDC check failed.',
        errorTitle: 'Host USB & Console Check Error',
        retry: () => this.runUsbCheck(),
      },
    );
  }

  // Card 2: Host Cable 180° Flip Test
  runHostReplug(): void {
    this.runCard(
      'hostFlip',
      () =>
        this.mauiService.testHostReplug(
          this.serialNumber,
          this.devicePort,
          this.hostUsbCheck.orientation,
        ),
      {
        delayMs: this.stabilizationDelayMs,
        isPassed: res => res.allPassed,
        onResult: res => {
          this.replugMessage = res.message || '';
          this.hostFlipCheck = toHostCheckResult(res);
        },
        failureTitle: 'Host Cable 180° Flip Test Failed',
        failureMessage: res =>
          res.message || 'Host cable flip verification failed.',
        errorTitle: 'Host Re-plug Test Error',
        retry: () => this.runHostReplug(),
      },
    );
  }

  // Card 3: RESET Button Test, part 1 (baseline uptime)
  captureResetBaseline(): void {
    if (this.otherUnitDetected) {
      return;
    }
    this.lockSerial();
    this.resetBaselineInProgress = true;
    this.resetStatus?.showWorking();
    this.cdr.detectChanges();

    const retry = () => this.captureResetBaseline();
    const bypass = () => {
      if (this.otherUnitDetected) {
        return;
      }
      this.resetBaselineCaptured = true;
      this.initialUptimeMs = RESET_BYPASS_BASELINE_UPTIME_MS;
      this.resetStatus?.showBypassed();
      this.cdr.detectChanges();
    };

    this.mauiService
      .testResetButton(
        this.serialNumber,
        this.devicePort,
        'check_initial_uptime',
      )
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: res => {
          this.resetBaselineInProgress = false;
          this.resetBaselineCaptured = res.uptimeSufficient;
          this.initialUptimeMs = parseInt(res.currentUptimeMs, 10) || 0;
          this.resetMessage = res.message || '';

          if (res.uptimeSufficient) {
            this.resetStatus?.showPassed();
          } else {
            this.resetStatus?.showFailed();
            this.dialogService.openFailure(
              'Reset Baseline Capture Failed',
              res.message || 'Initial uptime was not sufficient.',
              retry,
              bypass,
            );
          }
          this.cdr.detectChanges();
        },
        error: err => {
          this.resetBaselineInProgress = false;
          this.resetStatus?.showFailed();
          this.dialogService.openFailure(
            'Reset Baseline Error',
            err,
            retry,
            bypass,
          );
          this.cdr.detectChanges();
        },
      });
  }

  // Card 3: RESET Button Test, part 2 (reboot detection)
  verifyResetReboot(): void {
    this.runCard(
      'reset',
      () =>
        this.mauiService.testResetButton(
          this.serialNumber,
          this.devicePort,
          'verify_reboot',
          this.initialUptimeMs || RESET_DEFAULT_BASELINE_UPTIME_MS,
        ),
      {
        isPassed: res => res.resetVerified,
        onResult: res => {
          this.resetMessage = res.message || '';
        },
        failureTitle: 'Reset Button Test Failed',
        failureMessage: res =>
          res.message || 'Reboot was not detected after RESET button press.',
        errorTitle: 'Reset Button Test Error',
        retry: () => this.verifyResetReboot(),
      },
    );
  }

  // Card 4: BSL Button Test
  readBslButton(): void {
    this.runCard(
      'bsl',
      () => this.mauiService.testBslButton(this.serialNumber, this.devicePort),
      {
        isPassed: res => res.success,
        onResult: res => {
          this.peakBslMv = res.peakBslMv;
          this.bslMessage = res.message || '';
        },
        failureTitle: 'BSL Button Test Failed',
        failureMessage: res =>
          res.message || 'BSL ADC voltage did not rise above threshold.',
        errorTitle: 'BSL Button Test Error',
        retry: () => this.readBslButton(),
      },
    );
  }

  // Card 5: Visual LED Check
  confirmLeds(): void {
    if (this.otherUnitDetected) {
      return;
    }
    if (this.ledAmberChecked && this.ledGreenChecked) {
      this.markCardPassed('leds');
    } else {
      this.markCardFailed('leds', /* resetDone= */ true);
      this.openCardFailure(
        'leds',
        'LED Verification Incomplete',
        'Please visually inspect and confirm Amber LED is ON and Green LED is BLINKING at 1 Hz.',
        () => {},
      );
    }
    this.cdr.detectChanges();
  }

  // Card 6: Host + DUT + Charger Verification
  runHostDutCharger(): void {
    this.runCard(
      'hostDutCharger',
      () =>
        this.mauiService.runHostDutTests(
          this.serialNumber,
          this.serialNumber,
          'stage1_with_charger',
        ),
      {
        delayMs: this.stabilizationDelayMs,
        isPassed: res => res.allPassed,
        onResult: res => {
          this.hostDutOrientation = res.dutOrientation || '';
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
        },
        failureTitle: 'Host + DUT + Charger Test Failed',
        failureMessage: res => res.summary || 'Host+DUT verification failed.',
        errorTitle: 'Host + DUT Test Error',
        retry: () => this.runHostDutCharger(),
      },
    );
  }

  // Card 7: 180° SBU Cable Flip Test
  runDutSbuFlip(): void {
    this.runCard(
      'dutSbuFlip',
      () =>
        this.mauiService.runHostDutTests(
          this.serialNumber,
          this.serialNumber,
          'stage2_sbu_flip',
          this.hostDutOrientation,
        ),
      {
        delayMs: this.stabilizationDelayMs,
        isPassed: res => res.allPassed,
        onResult: res => {
          this.dutOrientation = res.dutOrientation || '';
        },
        failureTitle: '180° SBU Cable Flip Test Failed',
        failureMessage: res =>
          res.summary || 'SBU flip polarity verification failed.',
        errorTitle: '180° Flip Test Error',
        retry: () => this.runDutSbuFlip(),
      },
    );
  }

  // Card 8: Charger Disconnect Test
  runChargerDisconnect(): void {
    this.runCard(
      'chargerDisconnect',
      () =>
        this.mauiService.runHostDutTests(
          this.serialNumber,
          this.serialNumber,
          'stage3_no_charger',
        ),
      {
        delayMs: this.stabilizationDelayMs,
        isPassed: res => res.allPassed,
        onResult: res => {
          this.chargerDisconnected = res.chargerDisconnectedOk;
        },
        failureTitle: 'Charger Disconnect Test Failed',
        failureMessage: res =>
          res.summary || 'Charger disconnection check failed.',
        errorTitle: 'Charger Disconnect Error',
        retry: () => this.runChargerDisconnect(),
      },
    );
  }

  // Card 9: ADB Data Transfer Reliability
  runAdbTransfer(): void {
    this.runCard('adbTransfer', () => this.runAdbTransferRequest(), {
      isPassed: res => res.success,
      onResult: res => {
        this.adbTransferResult = res;
      },
      failureTitle: 'ADB Transfer Test Failed',
      failureMessage: res => res.message || 'ADB data transfer check failed.',
      errorTitle: 'ADB Transfer Test Error',
      retry: () => this.runAdbTransfer(),
    });
  }

  private runAdbTransferRequest(): Observable<MauiAdbTransferTestResponse> {
    this.adbTransferResult = null;
    // The DUT's ADB serial is the unit serial; sending it makes the backend
    // test exactly this unit instead of the first ADB device it finds.
    return this.mauiService.runAdbTransferTest(
      this.serialNumber,
      this.serialNumber,
    );
  }

  inspectLogs(): void {
    const targetSerial = this.serialNumber || 'UNKNOWN';
    this.inspectingLogs = true;
    this.deviceMgmtService
      .getDeviceLogsStatus(targetSerial)
      .pipe(takeUntilDestroyed(this.destroyRef))
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
  }

  /** True when every card of this mode really passed (bypass is not a pass). */
  get allCardsPassed(): boolean {
    return this.activeCards().every(card => this.isPassed(card));
  }

  /**
   * Overall verdict of the summary log: PASS when every active card passed,
   * FAIL when any card failed or was not run, otherwise PASS_WITH_BYPASS
   * (the backend maps PASS_WITH_BYPASS to BYPASSED).
   */
  get overallVerdict(): 'PASS' | 'FAIL' | 'PASS_WITH_BYPASS' {
    const statuses = this.activeCards().map(card => this.cards[card].status);
    if (statuses.every(status => status === 'PASSED')) {
      return 'PASS';
    }
    if (statuses.some(status => status === 'FAILED' || status === 'NOT_RUN')) {
      return 'FAIL';
    }
    return 'PASS_WITH_BYPASS';
  }

  private activeCards(): FunctionalCardId[] {
    return FUNCTIONAL_CARD_IDS.filter(
      card => this.isFatpMode || card !== 'barcode',
    );
  }

  /** Per-card results for MES (PASSED / FAILED / BYPASSED / NOT_RUN). */
  stageStatuses(): {[key: string]: string} {
    const statuses: {[key: string]: string} = {};
    for (const card of this.activeCards()) {
      statuses[FUNCTIONAL_STAGE_STATUS_KEYS[card]] = this.cards[card].status;
    }
    return statuses;
  }

  /** One-line description of the ADB transfer readings for the summary. */
  private adbTransferDetails(): string {
    const t = this.adbTransferResult;
    if (!t) {
      return 'no data';
    }
    return (
      `push ${t.pushMbPerS.toFixed(1)} MB/s, pull ${t.pullMbPerS.toFixed(1)} MB/s, ` +
      `${(Number(t.fileSizeBytes) / 1e6).toFixed(0)} MB, ${t.totalSeconds.toFixed(1)} s`
    );
  }

  private buildSummary(targetSerial: string, phaseTag: string): string {
    const c1 = this.hostUsbCheck;
    const c2 = this.hostFlipCheck;
    const s = this.cards;
    return (
      `=== Maui ${phaseTag} Verification Summary ===\n` +
      `Date: ${new Date().toISOString()}\n` +
      `Serial: ${targetSerial}\n` +
      `Mode: ${this.mode}\n` +
      (this.isFatpMode
        ? `Outer Barcode Scanned: ${this.scannedBarcode}\n`
        : '') +
      `Card 1 (Host USB & Console): ${s.hostUsb.status}\n` +
      `  MCU FW: ${this.mcuVersionStr}, PDC AppConfig: ${this.pdcAppConfigStr}\n` +
      `  HW Revision: ${c1.hwrev ?? 'N/A'} (${c1.hwrevOk ? 'PASS' : 'FAIL'}), Temp: ${c1.tempC?.toFixed(1) ?? 'N/A'}°C (${c1.tempOk ? 'PASS' : 'FAIL'})\n` +
      `  GPIO Verification: ${c1.gpiosOk ? 'PASS' : 'FAIL (' + c1.gpioDetails + ')'}\n` +
      `  Voltage Verification: ${c1.voltagesOk ? 'PASS' : 'FAIL (' + c1.voltageDetails + ')'}\n` +
      `Card 2 (Host 180° Flip): ${s.hostFlip.status} (${c1.orientation} -> ${c2.orientation})\n` +
      `  HW Revision: ${c2.hwrev ?? 'N/A'} (${c2.hwrevOk ? 'PASS' : 'FAIL'}), Temp: ${c2.tempC?.toFixed(1) ?? 'N/A'}°C (${c2.tempOk ? 'PASS' : 'FAIL'})\n` +
      `  GPIO Verification: ${c2.gpiosOk ? 'PASS' : 'FAIL (' + c2.gpioDetails + ')'}\n` +
      `  Voltage Verification: ${c2.voltagesOk ? 'PASS' : 'FAIL (' + c2.voltageDetails + ')'}\n` +
      `Card 3 (RESET Button): ${s.reset.status}\n` +
      `Card 4 (BSL Button ADC): ${s.bsl.status} (Peak BSL: ${this.peakBslMv} mV)\n` +
      `Card 5 (Visual LEDs): ${s.leds.status} (Amber=${this.ledAmberChecked}, Green_1Hz=${this.ledGreenChecked})\n` +
      `Card 6 (Host+DUT+Charger): ${s.hostDutCharger.status} (H2H=${this.h2hLinkSpeed}, PD=${this.negotiatedVoltage}V/${this.calculatedPowerW}W, CCD EC/GSC=${this.ecUartOk && this.gscUartOk})\n` +
      `Card 7 (180° SBU Flip): ${s.dutSbuFlip.status} (DUT SBU Orientation=${this.dutOrientation})\n` +
      `Card 8 (Charger Disconnected): ${s.chargerDisconnect.status} (Disconnected=${this.chargerDisconnected})\n` +
      `Card 9 (ADB Transfer, two ~400 MB random files, must finish within 60 s): ${s.adbTransfer.status} (${this.adbTransferDetails()})\n` +
      `OVERALL RESULT: ${this.overallVerdict}\n`
    );
  }

  private buildFinalResults(
    targetSerial: string,
  ): RecursivePartial<MauiFinalSubmissionRequest.AsObject> {
    return {
      serialNumber: targetSerial,
      operatorId: MAUI_OPERATOR_ID,
      stationId: this.isFatpMode
        ? MAUI_STATION_ID.fatpQt0
        : MAUI_STATION_ID.smtFunctional,
      // Provisioning (Step 1) is not verified by this station; don't claim it.
      mcuFlashed: false,
      ftdiProgrammed: false,
      pdcFlashed: false,
      hostOnlyPassed:
        this.isPassed('hostUsb') &&
        this.isPassed('hostFlip') &&
        this.isPassed('reset'),
      hostDutPassed:
        this.isPassed('hostDutCharger') &&
        this.isPassed('dutSbuFlip') &&
        this.isPassed('chargerDisconnect'),
      labelVerified: this.isFatpMode ? this.isPassed('barcode') : true,
      stageStatuses: this.stageStatuses(),
      adbSerial: this.adbSerial,
      ftdiSerial: this.ftdiSerial,
      scannedBarcode: this.isFatpMode ? this.scannedBarcode : '',
    };
  }

  // Card 10: Save Logs & Export
  saveLogs(): void {
    if (this.otherUnitDetected) {
      return;
    }
    if (this.deviceConnected) {
      this.dialogService.openFailure(
        'Unit Still Connected',
        'Please disconnect the Maui unit from the host USB port before saving logs and finalizing the report.',
        () => this.saveLogs(),
      );
      return;
    }
    if (this.savingLogs) {
      return;
    }

    this.savingLogs = true;
    this.completeStatus?.showWorking();
    this.cdr.detectChanges();

    const targetSerial =
      this.lastTestedSerial || this.serialNumber || 'UNKNOWN';
    const phaseTag = this.isFatpMode ? 'FATP_QT0' : 'SMT_FUNCTIONAL';
    const logFileName = this.isFatpMode
      ? 'fatp_qt0_summary'
      : 'smt_functional_summary';

    this.deviceMgmtService
      .saveCardLog(
        targetSerial,
        logFileName,
        this.buildSummary(targetSerial, phaseTag),
      )
      .pipe(
        requireSuccess('Saving the summary log failed.'),
        switchMap(() =>
          this.deviceMgmtService.submitFinalManufacturingResults(
            this.buildFinalResults(targetSerial),
          ),
        ),
        requireSuccess('Submitting the final results failed.'),
        takeUntilDestroyed(this.destroyRef),
      )
      .subscribe({
        next: res => {
          this.savingLogs = false;
          this.logsSaved = true;
          this.finalMesTrackingId = res.mesTrackingId || '';
          this.finalSummaryPath = res.summaryFilePath || '';
          this.finalResultsMessage = res.message || '';
          this.completeStatus?.showPassed();
          this.inspectLogs();
          this.cdr.detectChanges();
          this.scheduleReload();
        },
        error: err => {
          this.savingLogs = false;
          this.completeStatus?.showFailed();
          this.cdr.detectChanges();
          this.dialogService.openFailure('Log Archival Failed', err, () =>
            this.saveLogs(),
          );
        },
      });
  }

  /** Reloads the page for the next unit once the results are archived. */
  private scheduleReload(): void {
    timer(PAGE_RELOAD_DELAY_MS)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe(() => this.pageReload.reload());
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
