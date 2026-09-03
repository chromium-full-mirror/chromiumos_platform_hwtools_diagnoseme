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
import {MatIconModule} from '@angular/material/icon';
import {MatProgressBarModule} from '@angular/material/progress-bar';
import {Subscription, timer} from 'rxjs';
import {DiagnosemeReworkCard} from '../diagnoseme-rework-card/diagnoseme-rework-card';
import {DiagnosemeReworkCardColumn} from '../diagnoseme-rework-card-column/diagnoseme-rework-card-column';
import {MauiDialogService} from '../maui-common/maui-dialog.service';
import {autoAdvanceToCard} from '../maui-common/maui-scroll.utils';
import {MauiStation1Service} from '../services/maui-station1.service';
import {MauiPdcService} from '../services/maui-pdc.service';
import {MauiDeviceMgmtService} from '../services/maui-device-mgmt.service';
import {TestPassFailButton} from '../test-pass-fail-button/test-pass-fail-button';

export type ProvisioningStage =
  | 'IDLE'
  | 'MCU_FLASHING'
  | 'PDC_FLASHING'
  | 'FTDI_PROGRAMMING'
  | 'PROVISIONED'
  | 'FAILED';

export interface MauiSlotPipeline {
  slotIndex: number;
  devicePort: string;
  serialNumber: string;
  detectedPid: number;
  isDetected: boolean;
  stage: ProvisioningStage;
  pdcProgressPercent: number;
  currentBlock: string;
  statusMessage: string;
  errorMessage: string;
  hasAutoStarted: boolean;
  isPhysicallyDisconnected?: boolean;
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
  private readonly cdr = inject(ChangeDetectorRef);

  private readonly subs = new Subscription();
  private bslStreamSub?: Subscription;
  private disconnectMonitorSub?: Subscription;
  private readonly slotSettlingTimers = new Map<number, Subscription>();

  // Settings: Auto-provisioning is permanently enabled
  readonly autoProvisionEnabled = true;

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

  @ViewChild('pipelineStatus') pipelineStatus!: TestPassFailButton;
  @ViewChild('disconnectStatus') disconnectStatus!: TestPassFailButton;
  @ViewChild('saveLogsBtn', {read: ElementRef})
  saveLogsBtn?: ElementRef<HTMLButtonElement>;

  get hasCompletedSlots(): boolean {
    return this.slots.some(
      s => s.stage === 'PROVISIONED' || s.stage === 'FAILED',
    );
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
      detectedPid: 0x6001,
      isDetected: false,
      stage: 'IDLE',
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
              slot.detectedPid = res.pid || 0x6001;
              if (res.detectedSerial) {
                slot.serialNumber = res.detectedSerial;
              }

              // Auto-start pipeline immediately if eligible and enabled (Capture within 10s BSL window!)
              if (
                this.autoProvisionEnabled &&
                res.autoFlashEligible &&
                !slot.hasAutoStarted &&
                slot.stage === 'IDLE'
              ) {
                slot.hasAutoStarted = true;
                slot.statusMessage = `Board Detected on ${res.devicePort}. Starting MCU BSL Flashing immediately...`;
                this.cdr.detectChanges();

                if (this.slotSettlingTimers.has(slot.slotIndex)) {
                  this.slotSettlingTimers.get(slot.slotIndex)?.unsubscribe();
                  this.slotSettlingTimers.delete(slot.slotIndex);
                }

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
          const retrySub = timer(3000).subscribe(() =>
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
    this.disconnectMonitorSub = timer(0, 1500).subscribe(() => {
      this.pdcService.discoverConnectedPdcDevices().subscribe({
        next: res => {
          const devs = res.devices || [];
          this.connectedDevicesCount = devs.length;
          this.allDisconnected = devs.length === 0;

          // Track slot-level physical disconnection
          const activePorts = new Set(
            devs.map(d => d.devicePort).filter(p => !!p),
          );
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
        },
        error: () => {
          this.allDisconnected = false;
          this.cdr.detectChanges();
        },
      });
    });
    this.subs.add(this.disconnectMonitorSub);
  }

  startPipelineForSlot(slot: MauiSlotPipeline): void {
    slot.errorMessage = '';
    this.updateGlobalFlashingState();
    this.pipelineStatus?.showWorking();

    // Step 1: MCU Firmware Flashing
    slot.stage = 'MCU_FLASHING';
    slot.statusMessage = `Step 1/3: Flashing MCU firmware on ${slot.devicePort}...`;
    this.cdr.detectChanges();

    this.station1Service
      .autoProgramMcuBsl(slot.devicePort, 'stable', slot.serialNumber)
      .subscribe({
        next: mcuRes => {
          if (!mcuRes.success) {
            slot.stage = 'FAILED';
            slot.statusMessage = `MCU Flash Failed: ${mcuRes.message || 'Error'}`;
            slot.errorMessage = mcuRes.message;
            this.handleSlotFailure(slot, 'MCU Firmware Flashing Failed');
            return;
          }

          slot.statusMessage =
            'MCU Flashed. Waiting for USB 3.0 MUX settling (2.5s)...';
          this.cdr.detectChanges();

          // Wait 2.5s for MCU firmware to MUX USB 3.0 lines and USB bus to re-enumerate
          this.subs.add(
            timer(2500).subscribe(() => {
              this.station1Service
                .discoverTopologyAndSerial(slot.devicePort)
                .subscribe({
                  next: topoRes => {
                    if (topoRes.success && topoRes.readSerialNumber) {
                      slot.serialNumber = topoRes.readSerialNumber.trim();
                    }
                    // Step 2: PDC TPS66994 Firmware Flashing (Parallel stream)
                    this.runPdcFlashingForSlot(slot);
                  },
                  error: () => {
                    // Fallback to existing serial and proceed
                    this.runPdcFlashingForSlot(slot);
                  },
                });
            }),
          );
        },
        error: err => {
          slot.stage = 'FAILED';
          slot.statusMessage = 'MCU Flash Error';
          slot.errorMessage = err.message || 'gRPC communication failure';
          this.handleSlotFailure(slot, 'MCU Flash Error');
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
        'stable',
      )
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
              slot.statusMessage =
                'PDC Flashed (100%). Programming FTDI EEPROM...';
              this.cdr.detectChanges();

              // Step 3: FTDI EEPROM Programming
              this.runFtdiProgrammingForSlot(slot);
            } else {
              slot.stage = 'FAILED';
              slot.statusMessage = `PDC Flash Failed: ${progress.errorMessage}`;
              slot.errorMessage = progress.errorMessage;
              this.handleSlotFailure(slot, 'PDC Firmware Flashing Failed');
            }
          }
          this.cdr.detectChanges();
        },
        error: err => {
          slot.stage = 'FAILED';
          slot.statusMessage = 'PDC Flash Error';
          slot.errorMessage = err.message || 'gRPC communication failure';
          this.handleSlotFailure(slot, 'PDC Flash Error');
        },
      });
  }

  private runFtdiProgrammingForSlot(slot: MauiSlotPipeline): void {
    slot.stage = 'FTDI_PROGRAMMING';
    slot.statusMessage = 'Step 3/3: Programming FTDI EEPROM (PID 0x6002)...';
    this.cdr.detectChanges();

    this.station1Service.discoverTopologyAndSerial(slot.devicePort).subscribe({
      next: topoRes => {
        if (!topoRes.success || !topoRes.readSerialNumber) {
          slot.stage = 'FAILED';
          slot.statusMessage =
            topoRes.errorMessage ||
            `Could not read ADB electronic serial number on ${slot.devicePort}.`;
          slot.errorMessage = slot.statusMessage;
          this.handleSlotFailure(slot, 'ADB Serial Read Failed');
          return;
        }

        const serial = topoRes.readSerialNumber.trim();
        slot.serialNumber = serial;
        const oldSerial = topoRes.ftdiCurrentSerial || '';
        const oldPid = topoRes.ftdiCurrentPid || 0;

        this.station1Service
          .programFtdiEeprom(
            serial,
            slot.devicePort,
            0x0403,
            0x6002,
            'Maui Serial Console',
            oldSerial,
            oldPid,
          )
          .subscribe({
            next: ftdiRes => {
              if (ftdiRes.success) {
                slot.stage = 'PROVISIONED';
                slot.detectedPid = 0x6002;
                slot.statusMessage = `Fully Provisioned (${serial}) — Please unplug board`;
                this.checkCard1Completion();
              } else {
                slot.stage = 'FAILED';
                slot.statusMessage = `FTDI Programming Failed: ${ftdiRes.message}`;
                slot.errorMessage = ftdiRes.message;
                this.handleSlotFailure(slot, 'FTDI Programming Failed');
              }
              this.cdr.detectChanges();
            },
            error: err => {
              slot.stage = 'FAILED';
              slot.statusMessage = 'FTDI Programming Error';
              slot.errorMessage = err.message || 'gRPC communication failure';
              this.handleSlotFailure(slot, 'FTDI Programming Error');
            },
          });
      },
      error: err => {
        slot.stage = 'FAILED';
        slot.statusMessage = 'Topology & ADB Discovery Error';
        slot.errorMessage = err.message || 'gRPC communication failure';
        this.handleSlotFailure(slot, 'Topology Discovery Error');
      },
    });
  }

  private handleSlotFailure(slot: MauiSlotPipeline, title: string): void {
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
    this.pdcService.discoverConnectedPdcDevices().subscribe({
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

  private executeLogSave(): void {
    this.savingLogs = true;
    this.disconnectStatus.showWorking();
    this.cdr.detectChanges();

    const attemptedSlots = this.slots.filter(
      s => s.isDetected || s.serialNumber || s.stage !== 'IDLE',
    );

    // Save individual Step 1 log and submit final results for each unit with its unique ADB serial
    for (const slot of attemptedSlots) {
      const serial =
        slot.serialNumber || `PORT_${slot.devicePort.replace('/dev/', '')}`;
      const isPassed = slot.stage === 'PROVISIONED';
      const slotLog =
        '=== Maui SMT Step 1 Provisioning Record ===\n' +
        `Date: ${new Date().toISOString()}\n` +
        `Slot Index: ${slot.slotIndex}\n` +
        `Port: ${slot.devicePort}\n` +
        `Serial: ${serial}\n` +
        `Status: ${slot.stage}\n` +
        (slot.errorMessage ? `Error: ${slot.errorMessage}\n` : '');

      this.deviceMgmtService
        .saveCardLog(serial, 'step1_provisioning', slotLog)
        .subscribe({
          next: () => {
            this.deviceMgmtService
              .submitFinalManufacturingResults(
                serial,
                'OP_FACTORY',
                'STATION_SMT_STEP1',
                isPassed ||
                  slot.stage === 'PDC_FLASHING' ||
                  slot.stage === 'FTDI_PROGRAMMING',
                isPassed,
                isPassed || slot.stage === 'FTDI_PROGRAMMING',
                false,
                false,
                false,
                {
                  mcu_bsl_flashing:
                    slot.stage === 'FAILED' &&
                    slot.statusMessage.includes('MCU')
                      ? 'FAILED'
                      : 'PASSED',
                  pdc_fw_flashing:
                    slot.stage === 'FAILED' &&
                    slot.statusMessage.includes('PDC')
                      ? 'FAILED'
                      : slot.stage === 'PROVISIONED' ||
                          slot.stage === 'FTDI_PROGRAMMING'
                        ? 'PASSED'
                        : 'NOT_RUN',
                  ftdi_eeprom_programming: isPassed
                    ? 'PASSED'
                    : slot.stage === 'FAILED' &&
                        slot.statusMessage.includes('FTDI')
                      ? 'FAILED'
                      : 'NOT_RUN',
                },
                slot.serialNumber,
                slot.serialNumber,
                '',
              )
              .subscribe();
          },
        });
    }

    this.savingLogs = false;
    this.logsSaved = true;
    this.disconnectStatus.showPassed();
    this.cdr.detectChanges();
    // Auto refresh the page after 2.5s so fresh slots are ready for the next batch
    timer(2500).subscribe(() => {
      window.location.reload();
    });
  }
}
