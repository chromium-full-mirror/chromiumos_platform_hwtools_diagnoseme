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
  last,
  switchMap,
  tap,
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
  MAUI_OPERATOR_ID,
  MAUI_STATION_ID,
  MauiStepStatus,
  PAGE_RELOAD_DELAY_MS,
  RESET_BYPASS_BASELINE_UPTIME_MS,
  RESET_DEFAULT_BASELINE_UPTIME_MS,
  STABILIZATION_DELAY_MS,
  TEMPERATURE_RANGE_LABEL,
} from '../maui-constants';
import {MauiHwRev, MauiHwRevProfile, mauiHwRevProfile} from '../maui-hw-rev';
import {requireSuccess} from '../maui-rx.utils';
import {PageReloadService} from '../page-reload.service';
import {MauiStation2Service} from '../../services/maui-station2.service';
import {MauiDeviceMgmtService} from '../../services/maui-device-mgmt.service';
import {TestPassFailButton} from '../../test-pass-fail-button/test-pass-fail-button';
import {
  MauiSerialProvisionCardComponent,
  MauiSerialProvisionStatus,
} from '../maui-serial-provision-card/maui-serial-provision-card';
import {
  MauiH2hSpiCheckCardComponent,
  MauiH2hSpiCheckStatus,
} from '../maui-h2h-spi-check-card/maui-h2h-spi-check-card';
import {
  MauiAdbTransferTestResponse,
  MauiBslButtonProgress,
  MauiFinalSubmissionRequest,
  MauiHostOnlyTestResponse,
  MauiHostReplugTestResponse,
  MauiLogFileInfo,
  MauiReadSerialResponse,
} from '../../../proto/maui-manufacturing.pb';

/** Test cards of the functional suite, in operator order. */
export type FunctionalCardId =
  | 'serialProvision'
  | 'h2hSpi'
  | 'barcode'
  | 'hostUsb'
  | 'hostFlip'
  | 'reset'
  | 'bsl'
  | 'leds'
  | 'hostDutCharger'
  | 'dutSbuFlip'
  | 'adbTransfer'
  | 'chargerDisconnect';

/**
 * The ADB transfer runs right after the SBU flip card (Host + DUT + charger
 * still connected), before the charger disconnect card. The serial
 * provisioning and H2H SPI cards exist on Maui v1.2 only.
 */
export const FUNCTIONAL_CARD_IDS: readonly FunctionalCardId[] = [
  'serialProvision',
  'h2hSpi',
  'barcode',
  'hostUsb',
  'hostFlip',
  'reset',
  'bsl',
  'leds',
  'hostDutCharger',
  'dutSbuFlip',
  'adbTransfer',
  'chargerDisconnect',
];

/** Keys used for the per-card results in MES `stage_statuses`. */
export const FUNCTIONAL_STAGE_STATUS_KEYS: Record<FunctionalCardId, string> = {
  // Same keys as the backend's v1.2 stage logs.
  serialProvision: 'mcu_serial_provisioning',
  h2hSpi: 'h2h_spi_check',
  barcode: 'outer_barcode_verification',
  hostUsb: 'host_usb_console_check',
  hostFlip: 'host_cable_flip',
  reset: 'reset_button',
  bsl: 'bsl_button',
  leds: 'visual_leds',
  hostDutCharger: 'host_dut_charger',
  dutSbuFlip: 'dut_sbu_flip',
  // Same key as the backend's adb_transfer_test stage log.
  adbTransfer: 'adb_transfer_test',
  chargerDisconnect: 'charger_disconnect',
};

/**
 * Card number (before the revision offset) and name of each card, as used
 * in the summary log and on the final card. The barcode card has no number.
 */
const FUNCTIONAL_CARD_LABELS: Record<
  FunctionalCardId,
  {no: number | null; name: string}
> = {
  serialProvision: {no: -1, name: 'Serial Provisioning'},
  h2hSpi: {no: 0, name: 'H2H SPI Flash Access'},
  barcode: {no: null, name: 'Barcode'},
  hostUsb: {no: 1, name: 'Host USB & Console'},
  hostFlip: {no: 2, name: 'Host 180° Flip'},
  reset: {no: 3, name: 'RESET Button'},
  bsl: {no: 4, name: 'BSL Button'},
  leds: {no: 5, name: 'Visual LEDs'},
  hostDutCharger: {no: 6, name: 'Host+DUT+Charger'},
  dutSbuFlip: {no: 7, name: '180° SBU Flip'},
  adbTransfer: {no: 8, name: 'ADB Transfer'},
  chargerDisconnect: {no: 9, name: 'Charger Disconnected'},
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
  /** Accepted hwrev values shown to the operator (server's expected_hwrev). */
  expectedHwrev: string;
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
    expectedHwrev: '',
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

/**
 * |fallbackExpectedHwrev| is shown when the backend does not report
 * expected_hwrev.
 */
export function toHostCheckResult(
  res: MauiHostOnlyTestResponse | MauiHostReplugTestResponse,
  fallbackExpectedHwrev = '',
): HostCheckResult {
  return {
    orientation: res.hostOrientation || '',
    hwrev: res.detectedHwrev !== undefined ? res.detectedHwrev : null,
    hwrevOk: res.hwrevOk,
    expectedHwrev: res.expectedHwrev || fallbackExpectedHwrev,
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
    MauiH2hSpiCheckCardComponent,
    MauiSerialProvisionCardComponent,
    TestPassFailButton,
  ],
})
export class MauiFunctionalSuiteComponent implements OnInit {
  @Input() mode: 'smt' | 'fatp_qt0' = 'smt';
  @Input() suiteTitle = 'Maui Functional Verification';
  @Input() suiteSubtitle =
    'System-level functional testing for Maui hardware verification.';
  /** Hardware revision of the board under test (texts and extra cards). */
  @Input() hwRev: MauiHwRev = 'v1.1';

  private readonly mauiService = inject(MauiStation2Service);
  private readonly deviceMgmtService = inject(MauiDeviceMgmtService);
  private readonly dialogService = inject(MauiDialogService);
  private readonly pageReload = inject(PageReloadService);
  private readonly cdr = inject(ChangeDetectorRef);
  private readonly destroyRef = inject(DestroyRef);

  // Display-only expectations used by the template.
  readonly temperatureRangeLabel = TEMPERATURE_RANGE_LABEL;
  readonly bslThresholdMv = BSL_THRESHOLD_MV;
  readonly reloadDelaySec = PAGE_RELOAD_DELAY_MS / 1000;

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

  // Card 4: BSL Button Test (v1.1 ADC Status, v1.2 guided GPIO check)
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

  // Card 8: ADB Data Transfer Reliability (DUT through the H2H bridge, right
  // after the SBU flip card with Host + DUT + charger connected)
  adbTransferResult: MauiAdbTransferTestResponse | null = null;

  // Card 9: Charger Disconnect Verification (No Charger, after the ADB card)
  chargerDisconnected = false;

  // Serial Number Provisioning and H2H SPI Flash Access (Maui v1.2 only).
  // The standalone cards run the checks; their results land in `cards`.
  serialProvisionInProgress = false;
  h2hSpiInProgress = false;
  h2hSpiSerial = '';

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
  /** Verdict of the saved run: every card PASSED (bypassed = not passed). */
  finalPassed = false;
  /** Cards that did not pass, as shown on the final card ("<card>: <status>"). */
  finalNotPassedCards: string[] = [];

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
    // The v1.2 serial cards advance themselves (onSerialProvisionStatus,
    // onH2hSpiStatus) and have their own status buttons.
    serialProvision: {
      statusButton: () => undefined,
      nextCardElementId: 'func-card-h2h-spi',
      nextFocus: () => undefined,
    },
    h2hSpi: {
      statusButton: () => undefined,
      nextCardElementId: 'func-card-1',
      nextFocus: () => undefined,
    },
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
      nextCardElementId: 'func-card-adb',
      nextFocus: () => this.adbTransferBtn,
    },
    adbTransfer: {
      statusButton: () => this.adbTransferStatus,
      nextCardElementId: 'func-card-charger',
      nextFocus: () => this.chargerDisconnectBtn,
    },
    chargerDisconnect: {
      statusButton: () => this.chargerDisconnectStatus,
      nextCardElementId: 'func-card-logs',
      nextFocus: () => this.saveLogsBtn,
      onDone: () => this.inspectLogs(),
    },
  };

  get isFatpMode(): boolean {
    return this.mode === 'fatp_qt0';
  }

  get rev(): MauiHwRevProfile {
    return mauiHwRevProfile(this.hwRev);
  }

  /** Station directory of the final result packages of this revision. */
  get resultsDir(): string {
    return this.rev.resultsDir;
  }

  /** True while any card of the suite is running. */
  get anyTestInProgress(): boolean {
    return (
      this.resetBaselineInProgress ||
      this.serialProvisionInProgress ||
      this.h2hSpiInProgress ||
      FUNCTIONAL_CARD_IDS.some(card => this.cards[card].inProgress)
    );
  }

  /** Card 1 and the rest of the suite need the v1.2 serial cards done. */
  get serialCardsDone(): boolean {
    return (
      (!this.rev.requiresSerialProvisioning ||
        this.cards.serialProvision.done) &&
      (!this.rev.requiresH2hSpiCheck || this.cards.h2hSpi.done)
    );
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
    if (this.cards.bsl.status === 'PASSED') {
      return 'done';
    }
    if (!this.cards.bsl.inProgress || !this.bslPhase) {
      return '';
    }
    return n === this.bslPhase ? 'active' : n < this.bslPhase ? 'done' : '';
  }

  /** GPIO row text of a passing GPIO check (|details| from the backend). */
  gpioOkText(details: string): string {
    return this.rev.gpioOkText(details);
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
      this.cards.chargerDisconnect.done &&
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

  /** Result of the v1.2 serial provisioning card. */
  onSerialProvisionStatus(event: {
    status: MauiSerialProvisionStatus;
    serial: string;
  }): void {
    this.setChildCardStatus('serialProvision', event.status);
    if (event.serial && event.status === 'PASSED') {
      this.serialNumber = event.serial;
      this.lastTestedSerial = event.serial;
    }
    if (this.cards.serialProvision.done) {
      autoAdvanceToCard(
        this.serialCardsDone ? 'func-card-1' : 'func-card-h2h-spi',
      );
    }
    this.cdr.detectChanges();
  }

  /** Result of the v1.2 H2H SPI flash access card. */
  onH2hSpiStatus(event: {status: MauiH2hSpiCheckStatus; serial: string}): void {
    this.setChildCardStatus('h2hSpi', event.status);
    this.h2hSpiSerial = event.serial;
    if (this.serialCardsDone) {
      autoAdvanceToCard('func-card-1');
    }
    this.cdr.detectChanges();
  }

  /**
   * Records the result of a standalone card. A bypass only unlocks the next
   * card; it is reported as BYPASSED through the stage statuses.
   */
  private setChildCardStatus(
    card: 'serialProvision' | 'h2hSpi',
    status: MauiStepStatus,
  ): void {
    const state = this.cards[card];
    state.status = status;
    state.done = status === 'PASSED' || status === 'BYPASSED';
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
        this.statusMessage = `Unprogrammed Device Detected: ${this.serialNumber || 'Unknown'} (${this.rev.consoleLabel} PID: ${this.ftdiPid}) — ${this.rev.unprovisionedHint}`;
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
          this.hostUsbCheck = toHostCheckResult(
            res,
            this.rev.expectedHwrevText,
          );
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
          this.hostFlipCheck = toHostCheckResult(
            res,
            this.rev.expectedHwrevText,
          );
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
    if (this.rev.bslMethod === 'gpio') {
      this.runGuidedBslCheck();
      return;
    }
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

  /**
   * v1.2: guided check (not touched -> press and hold -> release) with the
   * live prompt and countdown streamed by the server. The card is judged on
   * the final progress message; a stream without one fails.
   */
  private runGuidedBslCheck(): void {
    this.bslMessage = '';
    this.bslFailedPhase = 0;
    this.bslPhase = 1;
    this.bslPrompt = 'Do not touch the BSL button';
    this.bslSecondsLeft = 0;
    this.bslLevel = -1;
    const noResult = 'BSL check ended without a result (MCU console lost?).';
    this.runCard(
      'bsl',
      () =>
        this.mauiService
          .streamBslButtonTest(this.serialNumber, this.devicePort)
          .pipe(
            tap(p => {
              this.bslPhase = p.phase;
              this.bslPrompt = p.prompt;
              this.bslSecondsLeft = p.secondsLeft;
              this.bslLevel = p.level;
              this.cdr.detectChanges();
            }),
            last(
              (p: MauiBslButtonProgress) => p.done,
              null as MauiBslButtonProgress | null,
            ),
          ),
      {
        isPassed: res => !!res?.success,
        onResult: res => {
          this.bslPhase = 0;
          this.peakBslMv = Math.max(res?.level ?? 0, 0);
          this.bslFailedPhase = res?.success ? 0 : res?.failedPhase || 0;
          this.bslMessage = res?.message || noResult;
        },
        onFailure: () => {
          this.bslPhase = 0;
        },
        failureTitle: 'BSL Button Test Failed',
        failureMessage: res => res?.message || noResult,
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
        this.rev.leds.incompleteMessage,
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

  // Card 9: Charger Disconnect Test (after the ADB transfer)
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

  // Card 8: ADB Data Transfer Reliability (after the SBU flip card)
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
    return FUNCTIONAL_CARD_IDS.filter(card => {
      switch (card) {
        case 'barcode':
          return this.isFatpMode;
        case 'serialProvision':
          return this.rev.requiresSerialProvisioning;
        case 'h2hSpi':
          return this.rev.requiresH2hSpiCheck;
        default:
          return true;
      }
    });
  }

  /** "Card <n> (<name>)" of the summary log and the final card. */
  private cardLabel(card: FunctionalCardId): string {
    const {no, name} = FUNCTIONAL_CARD_LABELS[card];
    return no === null ? name : `Card ${this.cardNo(no)} (${name})`;
  }

  /**
   * Cards of this run that did not pass, as "<card>: <status>". A bypass only
   * unlocks the next card: bypassed cards are listed here and fail the unit.
   */
  get notPassedCards(): string[] {
    return this.activeCards()
      .filter(card => !this.isPassed(card))
      .map(card => `${this.cardLabel(card)}: ${this.cards[card].status}`);
  }

  /** Per-card results for MES (PASSED / FAILED / BYPASSED / NOT_RUN). */
  stageStatuses(): {[key: string]: string} {
    const statuses: {[key: string]: string} = {};
    for (const card of this.activeCards()) {
      statuses[FUNCTIONAL_STAGE_STATUS_KEYS[card]] = this.cards[card].status;
    }
    return statuses;
  }

  /**
   * One-line result of the ADB transfer card, e.g. "transfer 25.1 s / limit
   * 40 s, 2 files 800 MiB, push 57.0 MB/s, pull 88.0 MB/s, link host/DUT
   * 5000 Mb/s". Fields not reported by the backend are left out.
   */
  adbSummary(t: MauiAdbTransferTestResponse): string {
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
    const host = Number(t.hostLinkMbps) || 0;
    const dut = Number(t.dutLinkMbps) || 0;
    if (host || dut) {
      const speeds = host === dut ? `${host}` : `${host || '?'}/${dut || '?'}`;
      parts.push(`link host/DUT ${speeds} Mb/s`);
    }
    return parts.join(', ');
  }

  private buildSummary(targetSerial: string, phaseTag: string): string {
    const c1 = this.hostUsbCheck;
    const c2 = this.hostFlipCheck;
    const s = this.cards;
    const n = (card: FunctionalCardId) => this.cardLabel(card);
    const notPassed = this.notPassedCards;
    const adbDetails = this.adbTransferResult
      ? this.adbSummary(this.adbTransferResult)
      : 'no data';
    return (
      `=== Maui ${phaseTag} Verification Summary ===\n` +
      `Date: ${new Date().toISOString()}\n` +
      `Serial: ${targetSerial}\n` +
      `Mode: ${this.mode}\n` +
      (this.isFatpMode
        ? `Outer Barcode Scanned: ${this.scannedBarcode}\n`
        : '') +
      (this.rev.requiresSerialProvisioning
        ? `${n('serialProvision')}: ${s.serialProvision.status}\n`
        : '') +
      (this.rev.requiresH2hSpiCheck
        ? `${n('h2hSpi')}: ${s.h2hSpi.status}` +
          (this.h2hSpiSerial ? ` (SPI serial ${this.h2hSpiSerial})` : '') +
          '\n'
        : '') +
      `${n('hostUsb')}: ${s.hostUsb.status}\n` +
      `  MCU FW: ${this.mcuVersionStr}, PDC AppConfig: ${this.pdcAppConfigStr}\n` +
      `  HW Revision: ${c1.hwrev ?? 'N/A'} (${c1.hwrevOk ? 'PASS' : 'FAIL'}), Temp: ${c1.tempC?.toFixed(1) ?? 'N/A'}°C (${c1.tempOk ? 'PASS' : 'FAIL'})\n` +
      `  GPIO Verification: ${c1.gpiosOk ? 'PASS' : 'FAIL (' + c1.gpioDetails + ')'}\n` +
      `  Voltage Verification: ${c1.voltagesOk ? 'PASS' : 'FAIL (' + c1.voltageDetails + ')'}\n` +
      `${n('hostFlip')}: ${s.hostFlip.status} (${c1.orientation} -> ${c2.orientation})\n` +
      `  HW Revision: ${c2.hwrev ?? 'N/A'} (${c2.hwrevOk ? 'PASS' : 'FAIL'}), Temp: ${c2.tempC?.toFixed(1) ?? 'N/A'}°C (${c2.tempOk ? 'PASS' : 'FAIL'})\n` +
      `  GPIO Verification: ${c2.gpiosOk ? 'PASS' : 'FAIL (' + c2.gpioDetails + ')'}\n` +
      `  Voltage Verification: ${c2.voltagesOk ? 'PASS' : 'FAIL (' + c2.voltageDetails + ')'}\n` +
      `${n('reset')}: ${s.reset.status}\n` +
      (this.rev.bslMethod === 'gpio'
        ? `Card ${this.cardNo(4)} (BSL Button GPIO, released -> pressed and held -> released): ${s.bsl.status}` +
          (this.bslFailedPhase
            ? ` (failed at step ${this.bslFailedPhase})`
            : '') +
          '\n'
        : `Card ${this.cardNo(4)} (BSL Button ADC): ${s.bsl.status} (Peak BSL: ${this.peakBslMv} mV)\n`) +
      `${n('leds')}: ${s.leds.status} (Amber=${this.ledAmberChecked}, Green_1Hz=${this.ledGreenChecked})\n` +
      `${n('hostDutCharger')}: ${s.hostDutCharger.status} (H2H=${this.h2hLinkSpeed}, PD=${this.negotiatedVoltage}V/${this.calculatedPowerW}W, CCD EC/GSC=${this.ecUartOk && this.gscUartOk})\n` +
      `${n('dutSbuFlip')}: ${s.dutSbuFlip.status} (DUT SBU Orientation=${this.dutOrientation})\n` +
      `Card ${this.cardNo(8)} (ADB Transfer, two ~400 MB random files): ${s.adbTransfer.status} (${adbDetails})\n` +
      `${n('chargerDisconnect')}: ${s.chargerDisconnect.status} (Disconnected=${this.chargerDisconnected})\n` +
      (notPassed.length ? `Not passed: ${notPassed.join(', ')}\n` : '') +
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
      // Only a passed barcode card (FATP QT0) verifies the label.
      labelVerified: this.isFatpMode && this.isPassed('barcode'),
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
    this.finalNotPassedCards = this.notPassedCards;
    this.finalPassed = this.finalNotPassedCards.length === 0;
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
          this.inspectLogs();
          if (this.finalPassed) {
            this.completeStatus?.showPassed();
            this.cdr.detectChanges();
            this.scheduleReload();
          } else {
            // A failed unit waits for "Next Unit" so that the operator sees
            // the failure and puts the unit aside.
            this.completeStatus?.showFailed();
            this.cdr.detectChanges();
          }
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

  /** "Next Unit" on the final card of a failed unit. */
  nextUnit(): void {
    this.pageReload.reload();
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
