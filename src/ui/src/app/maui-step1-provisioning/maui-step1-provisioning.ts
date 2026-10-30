// Copyright 2026 The ChromiumOS Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

import {
  ChangeDetectorRef,
  Component,
  DestroyRef,
  ElementRef,
  OnDestroy,
  OnInit,
  ViewChild,
  inject,
} from '@angular/core';
import {takeUntilDestroyed} from '@angular/core/rxjs-interop';
import {CommonModule} from '@angular/common';
import {FormsModule} from '@angular/forms';
import {MatButtonModule} from '@angular/material/button';
import {MatIconModule} from '@angular/material/icon';
import {MatProgressBarModule} from '@angular/material/progress-bar';
import {
  EMPTY,
  Observable,
  Subscription,
  catchError,
  exhaustMap,
  forkJoin,
  map,
  of,
  switchMap,
  timer,
} from 'rxjs';
import {DiagnosemeReworkCard} from '../diagnoseme-rework-card/diagnoseme-rework-card';
import {DiagnosemeReworkCardColumn} from '../diagnoseme-rework-card-column/diagnoseme-rework-card-column';
import {MauiDialogService} from '../maui-common/maui-dialog.service';
import {autoAdvanceToCard} from '../maui-common/maui-scroll.utils';
import {formatHumanReadableError} from '../maui-failure-dialog/maui-failure-dialog';
import {
  BSL_DISCOVERY_RETRY_MS,
  DISCONNECT_POLL_INTERVAL_MS,
  FTDI_PID_PROGRAMMED,
  FTDI_PID_UNPROGRAMMED,
  FTDI_PRODUCT_NAME,
  FTDI_VID,
  MAUI_FW_CHANNEL,
  MAUI_OPERATOR_ID,
  MAUI_RESULTS_DIR,
  MAUI_STATION_ID,
  MCU_SETTLE_DELAY_MS,
  MauiStepStatus,
  PAGE_RELOAD_DELAY_MS,
} from '../maui-common/maui-constants';
import {requireSuccess} from '../maui-common/maui-rx.utils';
import {PageReloadService} from '../maui-common/page-reload.service';
import {MauiStation1Service} from '../services/maui-station1.service';
import {MauiPdcService} from '../services/maui-pdc.service';
import {MauiDeviceMgmtService} from '../services/maui-device-mgmt.service';
import {TestPassFailButton} from '../test-pass-fail-button/test-pass-fail-button';
import {
  MauiConnectedPdcDevicesResponse,
  MauiFinalSubmissionResponse,
} from '../../proto/maui-manufacturing.pb';

export type ProvisioningStage =
  | 'IDLE'
  | 'MCU_FLASHING'
  | 'PDC_FLASHING'
  | 'FTDI_PROGRAMMING'
  | 'PROVISIONED'
  | 'FAILED';

/** The three provisioning steps of a slot, in pipeline order. */
export type ProvisioningStep = 'mcu' | 'pdc' | 'ftdi';

/** Keys used for the per-step results in MES `stage_statuses`. */
export const PROVISIONING_STAGE_STATUS_KEYS: Record<ProvisioningStep, string> =
  {
    mcu: 'mcu_bsl_flashing',
    pdc: 'pdc_fw_flashing',
    ftdi: 'ftdi_eeprom_programming',
  };

export interface MauiSlotPipeline {
  slotIndex: number;
  devicePort: string;
  serialNumber: string;
  ftdiInitialSerial?: string;
  /** FTDI serial written by a successful EEPROM programming (= ADB serial). */
  ftdiProgrammedSerial?: string;
  detectedPid: number;
  isDetected: boolean;
  stage: ProvisioningStage;
  /** Result of each provisioning step of the last pipeline run. */
  stepStatus: Record<ProvisioningStep, MauiStepStatus>;
  /** The step that failed, if the slot is FAILED. */
  failedStep?: ProvisioningStep;
  pdcProgressPercent: number;
  currentBlock: string;
  statusMessage: string;
  errorMessage: string;
  hasAutoStarted: boolean;
  isPhysicallyDisconnected?: boolean;
  /** The slot's Step 1 log and MES result were archived successfully. */
  resultsArchived?: boolean;
  /** Where the backend archived the results (MauiFinalSubmissionResponse.message). */
  archiveMessage?: string;
}

function notRunSteps(): Record<ProvisioningStep, MauiStepStatus> {
  return {mcu: 'NOT_RUN', pdc: 'NOT_RUN', ftdi: 'NOT_RUN'};
}

@Component({
  selector: 'app-maui-step1-provisioning',
  standalone: true,
  templateUrl: './maui-step1-provisioning.html',
  styleUrl: './maui-step1-provisioning.scss',
  imports: [
    CommonModule,
    DiagnosemeReworkCard,
    DiagnosemeReworkCardColumn,
    FormsModule,
    MatButtonModule,
    MatIconModule,
    MatProgressBarModule,
    TestPassFailButton,
  ],
})
export class MauiStep1ProvisioningComponent implements OnInit, OnDestroy {
  private readonly station1Service = inject(MauiStation1Service);
  private readonly pdcService = inject(MauiPdcService);
  private readonly deviceMgmtService = inject(MauiDeviceMgmtService);
  private readonly dialogService = inject(MauiDialogService);
  private readonly pageReload = inject(PageReloadService);
  private readonly cdr = inject(ChangeDetectorRef);
  private readonly destroyRef = inject(DestroyRef);

  private readonly subs = new Subscription();
  private bslStreamSub?: Subscription;
  private disconnectMonitorSub?: Subscription;
  private readonly slotSettlingTimers = new Map<number, Subscription>();

  readonly reloadDelaySec = PAGE_RELOAD_DELAY_MS / 1000;
  readonly resultsDir = MAUI_RESULTS_DIR;

  // Card 1: 4-Slot Pipeline State (MCU -> PDC -> FTDI)
  isAnyFlashing = false;
  pipelineCardPassed = false;

  slots: MauiSlotPipeline[] = [
    this.createSlot(1),
    this.createSlot(2),
    this.createSlot(3),
    this.createSlot(4),
  ];

  // Card 2: Disconnect Verification & Log Archival
  connectedDevicesCount = 0;
  allDisconnected = false;
  savingLogs = false;
  logsSaved = false;

  @ViewChild('pipelineStatus') pipelineStatus?: TestPassFailButton;
  @ViewChild('disconnectStatus') disconnectStatus?: TestPassFailButton;
  @ViewChild('saveLogsBtn', {read: ElementRef})
  saveLogsBtn?: ElementRef<HTMLButtonElement>;

  get hasCompletedSlots(): boolean {
    return this.slots.some(
      s => s.stage === 'PROVISIONED' || s.stage === 'FAILED',
    );
  }

  /** Archive locations reported by the backend for this batch. */
  get archiveMessages(): string[] {
    return this.slots
      .map(s => s.archiveMessage || '')
      .filter(message => !!message);
  }

  get canSaveLogs(): boolean {
    return (
      this.hasCompletedSlots &&
      !this.isAnyFlashing &&
      this.allDisconnected &&
      !this.savingLogs &&
      !this.logsSaved
    );
  }

  ngOnInit(): void {
    this.startBslDiscoveryStream();
  }

  ngOnDestroy(): void {
    this.slotSettlingTimers.forEach(sub => sub.unsubscribe());
    this.slotSettlingTimers.clear();
    this.subs.unsubscribe();
  }

  createSlot(slotIndex: number, defaultPort = ''): MauiSlotPipeline {
    return {
      slotIndex,
      devicePort: defaultPort,
      serialNumber: '',
      ftdiInitialSerial: '',
      ftdiProgrammedSerial: '',
      detectedPid: FTDI_PID_UNPROGRAMMED,
      isDetected: false,
      stage: 'IDLE',
      stepStatus: notRunSteps(),
      pdcProgressPercent: 0,
      currentBlock: '',
      statusMessage: 'Slot Empty — Awaiting board connection',
      errorMessage: '',
      hasAutoStarted: false,
      isPhysicallyDisconnected: false,
    };
  }

  startBslDiscoveryStream(): void {
    if (this.bslStreamSub && !this.bslStreamSub.closed) {
      this.bslStreamSub.unsubscribe();
    }

    this.bslStreamSub = this.station1Service
      .streamBslAutoDiscovery()
      .subscribe({
        next: res => {
          if (this.pipelineCardPassed) {
            return;
          }

          if (res.bslDetected && res.devicePort) {
            let slot = this.slots.find(s => s.devicePort === res.devicePort);
            if (!slot) {
              slot = this.slots.find(s => !s.devicePort && s.stage === 'IDLE');
            }
            if (!slot) {
              slot = this.slots.find(s => !s.isDetected && s.stage === 'IDLE');
            }
            if (slot) {
              slot.isDetected = true;
              slot.devicePort = res.devicePort;
              slot.detectedPid = res.pid || FTDI_PID_UNPROGRAMMED;
              if (res.ftdiSerial) {
                slot.ftdiInitialSerial = res.ftdiSerial;
              } else if (res.detectedSerial && !slot.ftdiInitialSerial) {
                slot.ftdiInitialSerial = res.detectedSerial;
              }
              if (res.detectedSerial) {
                slot.serialNumber = res.detectedSerial;
              }

              // Auto-start the pipeline right away (capture within the 10s BSL window!)
              if (
                res.autoFlashEligible &&
                !slot.hasAutoStarted &&
                slot.stage === 'IDLE'
              ) {
                slot.hasAutoStarted = true;
                slot.statusMessage = `Board Detected on ${res.devicePort}. Starting MCU BSL Flashing immediately...`;
                this.cdr.detectChanges();

                this.cancelSettlingTimer(slot);
                this.startPipelineForSlot(slot);
              } else if (slot.stage === 'IDLE') {
                slot.statusMessage = res.autoFlashEligible
                  ? `Unprogrammed MCU Detected on ${res.devicePort} (BSL Ready)`
                  : `Board Detected on ${res.devicePort} (PID 0x${slot.detectedPid.toString(16)})`;
              }
            }
          }
          this.updateGlobalFlashingState();
          this.cdr.detectChanges();
        },
        error: () => {
          // Reconnect discovery if interrupted
          const retrySub = timer(BSL_DISCOVERY_RETRY_MS).subscribe(() =>
            this.startBslDiscoveryStream(),
          );
          this.subs.add(retrySub);
        },
      });

    this.subs.add(this.bslStreamSub);
  }

  startDisconnectMonitor(): void {
    if (this.disconnectMonitorSub && !this.disconnectMonitorSub.closed) {
      return;
    }
    this.disconnectMonitorSub = timer(0, DISCONNECT_POLL_INTERVAL_MS)
      .pipe(
        exhaustMap(() =>
          this.pdcService.discoverConnectedPdcDevices().pipe(
            catchError(() => {
              this.allDisconnected = false;
              this.cdr.detectChanges();
              return EMPTY;
            }),
          ),
        ),
      )
      .subscribe(res => this.applyConnectedDevices(res));
    this.subs.add(this.disconnectMonitorSub);
  }

  private applyConnectedDevices(res: MauiConnectedPdcDevicesResponse): void {
    const devs = res.devices || [];
    this.connectedDevicesCount = devs.length;
    this.allDisconnected = devs.length === 0;

    // Track slot-level physical disconnection
    const activePorts = new Set(devs.map(d => d.devicePort).filter(p => !!p));
    const activeSerials = new Set(
      devs.map(d => d.serialNumber).filter(s => !!s),
    );

    for (const slot of this.slots) {
      if (slot.stage === 'PROVISIONED' || slot.stage === 'FAILED') {
        const isStillPlugged =
          (slot.devicePort && activePorts.has(slot.devicePort)) ||
          (slot.serialNumber && activeSerials.has(slot.serialNumber));
        slot.isPhysicallyDisconnected = !isStillPlugged;
      }
    }

    this.cdr.detectChanges();
  }

  private cancelSettlingTimer(slot: MauiSlotPipeline): void {
    this.slotSettlingTimers.get(slot.slotIndex)?.unsubscribe();
    this.slotSettlingTimers.delete(slot.slotIndex);
  }

  startPipelineForSlot(slot: MauiSlotPipeline): void {
    slot.errorMessage = '';
    slot.stepStatus = notRunSteps();
    slot.failedStep = undefined;
    slot.ftdiProgrammedSerial = '';
    this.updateGlobalFlashingState();
    this.pipelineStatus?.showWorking();

    // Step 1: MCU Firmware Flashing
    slot.stage = 'MCU_FLASHING';
    slot.statusMessage = `Step 1/3: Flashing MCU firmware on ${slot.devicePort}...`;
    this.cdr.detectChanges();

    this.station1Service
      .autoProgramMcuBsl(slot.devicePort, MAUI_FW_CHANNEL, slot.serialNumber)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: mcuRes => {
          if (!mcuRes.success) {
            this.failSlot(
              slot,
              'mcu',
              `MCU Flash Failed: ${mcuRes.message || 'Error'}`,
              mcuRes.message,
              'MCU Firmware Flashing Failed',
            );
            return;
          }

          if (mcuRes.ftdiSerial) {
            slot.ftdiInitialSerial = mcuRes.ftdiSerial;
          }

          slot.statusMessage = `MCU Flashed. Waiting ${MCU_SETTLE_DELAY_MS / 1000}s for USB 3.0 MUX & console re-enumeration...`;
          this.cdr.detectChanges();

          this.cancelSettlingTimer(slot);

          // Settling delay for MCU firmware to boot and USB 3.0 MUX to settle
          const settleSub = timer(MCU_SETTLE_DELAY_MS).subscribe(() => {
            this.slotSettlingTimers.delete(slot.slotIndex);
            this.verifyConsoleAfterMcuFlash(slot);
          });
          this.slotSettlingTimers.set(slot.slotIndex, settleSub);
          this.subs.add(settleSub);
        },
        error: err => {
          this.failSlot(
            slot,
            'mcu',
            'MCU Flash Error',
            err.message || 'gRPC communication failure',
            'MCU Flash Error',
          );
        },
      });
  }

  /** Checks that the FTDI console re-enumerated after the MCU booted. */
  private verifyConsoleAfterMcuFlash(slot: MauiSlotPipeline): void {
    slot.statusMessage = `Verifying FTDI console re-enumeration (serial: ${slot.ftdiInitialSerial || 'any'})...`;
    this.cdr.detectChanges();

    this.station1Service
      .discoverTopologyAndSerial(
        slot.devicePort,
        '',
        slot.ftdiInitialSerial || '',
      )
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: topoRes => {
          if (topoRes.success && topoRes.ftdiDevNode) {
            if (topoRes.ftdiDevNode !== slot.devicePort) {
              slot.statusMessage = `Console re-enumerated from ${slot.devicePort} to ${topoRes.ftdiDevNode}. Starting Step 2/3: TPS66994 PDC Flashing...`;
              slot.devicePort = topoRes.ftdiDevNode;
            } else {
              slot.statusMessage = `Console confirmed on ${slot.devicePort}. Starting Step 2/3: TPS66994 PDC Flashing...`;
            }
            if (topoRes.readSerialNumber) {
              slot.serialNumber = topoRes.readSerialNumber.trim();
            }
            if (topoRes.ftdiCurrentSerial) {
              slot.ftdiInitialSerial = topoRes.ftdiCurrentSerial.trim();
            }
            slot.stepStatus.mcu = 'PASSED';
            this.cdr.detectChanges();
            this.runPdcFlashingForSlot(slot);
          } else if (topoRes.ftdiDevNode) {
            slot.devicePort = topoRes.ftdiDevNode;
            slot.statusMessage = `Console found on ${slot.devicePort}. Starting Step 2/3: TPS66994 PDC Flashing...`;
            slot.stepStatus.mcu = 'PASSED';
            this.cdr.detectChanges();
            this.runPdcFlashingForSlot(slot);
          } else {
            // The flashed MCU did not come back: count it as an MCU step failure.
            this.failSlot(
              slot,
              'mcu',
              `Console Re-enumeration Failed: ${topoRes.errorMessage || 'Port lost'}`,
              topoRes.errorMessage ||
                'FTDI console port disappeared after MCU boot',
              'Console Re-enumeration Failed',
            );
          }
        },
        error: err => {
          slot.statusMessage = `Console check skipped (${err.message}). Starting Step 2/3: PDC Flashing...`;
          slot.stepStatus.mcu = 'PASSED';
          this.cdr.detectChanges();
          this.runPdcFlashingForSlot(slot);
        },
      });
  }

  private runPdcFlashingForSlot(slot: MauiSlotPipeline): void {
    slot.stage = 'PDC_FLASHING';
    slot.pdcProgressPercent = 0;
    slot.statusMessage = 'Step 2/3: Flashing TPS66994 PDC firmware...';
    this.cdr.detectChanges();

    this.pdcService
      .programSinglePdc(
        slot.slotIndex,
        slot.serialNumber || `MAUI-SLOT${slot.slotIndex}`,
        slot.devicePort,
        MAUI_FW_CHANNEL,
      )
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: progress => {
          slot.pdcProgressPercent = progress.progressPercent;
          slot.currentBlock = progress.currentBlock;
          slot.statusMessage =
            progress.statusMessage ||
            `Flashing PDC... ${progress.progressPercent}%`;

          if (progress.isComplete) {
            if (!progress.errorMessage) {
              slot.pdcProgressPercent = 100;
              slot.stepStatus.pdc = 'PASSED';
              slot.statusMessage =
                'PDC Flashed (100%). Programming FTDI EEPROM...';
              this.cdr.detectChanges();

              // Step 3: FTDI EEPROM Programming
              this.runFtdiProgrammingForSlot(slot);
            } else {
              this.failSlot(
                slot,
                'pdc',
                `PDC Flash Failed: ${progress.errorMessage}`,
                progress.errorMessage,
                'PDC Firmware Flashing Failed',
              );
            }
          }
          this.cdr.detectChanges();
        },
        error: err => {
          this.failSlot(
            slot,
            'pdc',
            'PDC Flash Error',
            err.message || 'gRPC communication failure',
            'PDC Flash Error',
          );
        },
      });
  }

  private runFtdiProgrammingForSlot(slot: MauiSlotPipeline): void {
    slot.stage = 'FTDI_PROGRAMMING';
    slot.statusMessage = `Step 3/3: Programming FTDI EEPROM (PID 0x${FTDI_PID_PROGRAMMED.toString(16)})...`;
    this.cdr.detectChanges();

    this.station1Service
      .discoverTopologyAndSerial(
        slot.devicePort,
        '',
        slot.ftdiInitialSerial || '',
      )
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: topoRes => {
          const targetSerial = (
            topoRes.readSerialNumber ||
            slot.serialNumber ||
            ''
          ).trim();
          if (!targetSerial) {
            const message =
              topoRes.errorMessage ||
              `Could not read ADB electronic serial number on ${slot.devicePort}.`;
            this.failSlot(
              slot,
              'ftdi',
              message,
              message,
              'ADB Serial Read Failed',
            );
            return;
          }

          slot.serialNumber = targetSerial;
          if (topoRes.ftdiDevNode && topoRes.ftdiDevNode !== slot.devicePort) {
            slot.devicePort = topoRes.ftdiDevNode;
          }
          const oldSerial =
            topoRes.ftdiCurrentSerial || slot.ftdiInitialSerial || '';
          const oldPid = topoRes.ftdiCurrentPid || 0;

          this.station1Service
            .programFtdiEeprom(
              targetSerial,
              slot.devicePort,
              FTDI_VID,
              FTDI_PID_PROGRAMMED,
              FTDI_PRODUCT_NAME,
              oldSerial,
              oldPid,
            )
            .pipe(takeUntilDestroyed(this.destroyRef))
            .subscribe({
              next: ftdiRes => {
                if (ftdiRes.success) {
                  slot.stage = 'PROVISIONED';
                  slot.stepStatus.ftdi = 'PASSED';
                  slot.detectedPid = FTDI_PID_PROGRAMMED;
                  slot.ftdiProgrammedSerial = targetSerial;
                  slot.statusMessage = `Fully Provisioned (${targetSerial}) — Please unplug board`;
                  this.checkCard1Completion();
                } else {
                  this.failSlot(
                    slot,
                    'ftdi',
                    `FTDI Programming Failed: ${ftdiRes.message}`,
                    ftdiRes.message,
                    'FTDI Programming Failed',
                  );
                }
                this.cdr.detectChanges();
              },
              error: err => {
                this.failSlot(
                  slot,
                  'ftdi',
                  'FTDI Programming Error',
                  err.message || 'gRPC communication failure',
                  'FTDI Programming Error',
                );
              },
            });
        },
        error: err => {
          this.failSlot(
            slot,
            'ftdi',
            'Topology & ADB Discovery Error',
            err.message || 'gRPC communication failure',
            'Topology Discovery Error',
          );
        },
      });
  }

  /** Marks `step` of the slot as failed and offers a retry to the operator. */
  private failSlot(
    slot: MauiSlotPipeline,
    step: ProvisioningStep,
    statusMessage: string,
    errorMessage: string,
    title: string,
  ): void {
    slot.stage = 'FAILED';
    slot.stepStatus[step] = 'FAILED';
    slot.failedStep = step;
    slot.statusMessage = statusMessage;
    slot.errorMessage = errorMessage;

    this.updateGlobalFlashingState();
    this.checkCard1Completion();
    this.cdr.detectChanges();

    this.dialogService.openFailure(
      `Slot ${slot.slotIndex}: ${title}`,
      slot.errorMessage || slot.statusMessage,
      () => this.startPipelineForSlot(slot),
    );
  }

  private updateGlobalFlashingState(): void {
    this.isAnyFlashing = this.slots.some(
      s =>
        s.stage === 'MCU_FLASHING' ||
        s.stage === 'PDC_FLASHING' ||
        s.stage === 'FTDI_PROGRAMMING',
    );
  }

  private checkCard1Completion(): void {
    this.updateGlobalFlashingState();

    if (this.hasCompletedSlots && !this.isAnyFlashing) {
      this.pipelineCardPassed = true;
      const anyFailed = this.slots.some(s => s.stage === 'FAILED');
      if (anyFailed) {
        this.pipelineStatus?.showFailed();
      } else {
        this.pipelineStatus?.showPassed();
      }
      this.startDisconnectMonitor();
      autoAdvanceToCard('step1-card-2', this.saveLogsBtn);
    } else if (this.isAnyFlashing) {
      this.pipelineStatus?.showWorking();
    }
    this.cdr.detectChanges();
  }

  // Card 2: Disconnect Verification & Log Archival
  saveBatchLogsAndFinalize(): void {
    if (this.savingLogs) {
      return;
    }
    this.pdcService
      .discoverConnectedPdcDevices()
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: res => {
          const devs = res.devices || [];
          this.connectedDevicesCount = devs.length;
          this.allDisconnected = devs.length === 0;
          this.cdr.detectChanges();

          if (devs.length > 0) {
            const names = devs
              .map(d => d.serialNumber || d.productDescription || 'Maui Board')
              .join(', ');
            this.dialogService.openFailure(
              'Units Still Connected (ADB / Hardware Active)',
              `Please physically disconnect ALL provisioned Maui boards from host USB ports before saving logs and finalizing Step 1 (${devs.length} unit(s) still detected: ${names}).`,
              () => this.saveBatchLogsAndFinalize(),
            );
            return;
          }

          this.executeLogSave();
        },
        error: () => {
          if (!this.allDisconnected || this.connectedDevicesCount > 0) {
            this.dialogService.openFailure(
              'Units Still Connected',
              'Please disconnect ALL provisioned Maui units before saving logs and finalizing Step 1.',
              () => this.saveBatchLogsAndFinalize(),
            );
            return;
          }
          this.executeLogSave();
        },
      });
  }

  private buildSlotLog(slot: MauiSlotPipeline, serial: string): string {
    return (
      '=== Maui SMT Step 1 Provisioning Record ===\n' +
      `Date: ${new Date().toISOString()}\n` +
      `Slot Index: ${slot.slotIndex}\n` +
      `Port: ${slot.devicePort}\n` +
      `Serial: ${serial}\n` +
      `Status: ${slot.stage}\n` +
      (slot.errorMessage ? `Error: ${slot.errorMessage}\n` : '')
    );
  }

  /** Saves the Step 1 log and submits the MES result of one slot. */
  private archiveSlotResults(
    slot: MauiSlotPipeline,
  ): Observable<MauiFinalSubmissionResponse> {
    const serial =
      slot.serialNumber || `PORT_${slot.devicePort.replace('/dev/', '')}`;
    const stageStatuses: {[key: string]: string} = {};
    for (const step of Object.keys(
      PROVISIONING_STAGE_STATUS_KEYS,
    ) as ProvisioningStep[]) {
      stageStatuses[PROVISIONING_STAGE_STATUS_KEYS[step]] =
        slot.stepStatus[step];
    }

    return this.deviceMgmtService
      .saveCardLog(
        serial,
        'step1_provisioning',
        this.buildSlotLog(slot, serial),
      )
      .pipe(
        requireSuccess(
          `Saving the Step 1 log of slot ${slot.slotIndex} failed.`,
        ),
        switchMap(() =>
          this.deviceMgmtService.submitFinalManufacturingResults({
            serialNumber: serial,
            operatorId: MAUI_OPERATOR_ID,
            stationId: MAUI_STATION_ID.smtStep1,
            mcuFlashed: slot.stepStatus.mcu === 'PASSED',
            ftdiProgrammed: slot.stepStatus.ftdi === 'PASSED',
            pdcFlashed: slot.stepStatus.pdc === 'PASSED',
            hostOnlyPassed: false,
            hostDutPassed: false,
            labelVerified: false,
            stageStatuses,
            adbSerial: slot.serialNumber,
            // The backend compares adb_serial and ftdi_serial (serials_matched).
            ftdiSerial:
              slot.ftdiProgrammedSerial || slot.ftdiInitialSerial || '',
            scannedBarcode: '',
          }),
        ),
        requireSuccess(
          `Submitting the Step 1 result of slot ${slot.slotIndex} failed.`,
        ),
      );
  }

  private executeLogSave(): void {
    this.savingLogs = true;
    this.disconnectStatus?.showWorking();
    this.cdr.detectChanges();

    // Slots archived by an earlier (partially failed) attempt are not re-sent.
    const pendingSlots = this.slots.filter(
      s =>
        (s.isDetected || s.serialNumber || s.stage !== 'IDLE') &&
        !s.resultsArchived,
    );

    // Archive every unit with its own ADB serial; wait for all of them.
    const results$ =
      pendingSlots.length === 0
        ? of([])
        : forkJoin(
            pendingSlots.map(slot =>
              this.archiveSlotResults(slot).pipe(
                map(res => {
                  slot.resultsArchived = true;
                  slot.archiveMessage = res.message || '';
                  return null;
                }),
                catchError((err: unknown) =>
                  of(
                    `Slot ${slot.slotIndex}: ${formatHumanReadableError(err)}`,
                  ),
                ),
              ),
            ),
          );

    results$.pipe(takeUntilDestroyed(this.destroyRef)).subscribe(results => {
      this.savingLogs = false;
      const failures = results.filter((r): r is string => !!r);
      if (failures.length > 0) {
        this.disconnectStatus?.showFailed();
        this.cdr.detectChanges();
        this.dialogService.openFailure(
          'Step 1 Log Archival Failed',
          failures.join('\n'),
          () => this.executeLogSave(),
        );
        return;
      }
      this.logsSaved = true;
      this.disconnectStatus?.showPassed();
      this.cdr.detectChanges();
      // Reload the page so fresh slots are ready for the next batch
      timer(PAGE_RELOAD_DELAY_MS)
        .pipe(takeUntilDestroyed(this.destroyRef))
        .subscribe(() => this.pageReload.reload());
    });
  }
}
