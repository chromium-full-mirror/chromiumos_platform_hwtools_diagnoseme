// Copyright 2026 The ChromiumOS Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

import {
  ChangeDetectorRef,
  Component,
  OnDestroy,
  OnInit,
  inject,
} from '@angular/core';
import {CommonModule} from '@angular/common';
import {MatButtonModule} from '@angular/material/button';
import {MatIconModule} from '@angular/material/icon';
import {MatProgressBarModule} from '@angular/material/progress-bar';
import {Subscription} from 'rxjs';
import {DiagnosemeReworkCard} from '../diagnoseme-rework-card/diagnoseme-rework-card';
import {DiagnosemeReworkCardColumn} from '../diagnoseme-rework-card-column/diagnoseme-rework-card-column';
import {MauiDialogService} from '../maui-common/maui-dialog.service';
import {MauiStationFooterComponent} from '../maui-common/maui-station-footer/maui-station-footer';
import {MauiPdcService} from '../services/maui-pdc.service';
import {MauiDeviceMgmtService} from '../services/maui-device-mgmt.service';

export interface DeviceSlotState {
  slotIndex: number;
  serialNumber: string;
  devicePort: string;
  containerId: string;
  productDescription: string;
  isDetected: boolean;
  isFlashing: boolean;
  isPassed: boolean;
  isFailed: boolean;
  isBypassed: boolean;
  progressPercent: number;
  currentBlock: string;
  statusMessage: string;
  errorMessage: string;
  logContent: string;
}

@Component({
  selector: 'app-maui-pdc-flashing',
  standalone: true,
  templateUrl: './maui-pdc-flashing.html',
  styleUrl: './maui-pdc-flashing.scss',
  imports: [
    CommonModule,
    DiagnosemeReworkCard,
    DiagnosemeReworkCardColumn,
    MatButtonModule,
    MatIconModule,
    MatProgressBarModule,
    MauiStationFooterComponent,
  ],
})
export class MauiPdcFlashingComponent implements OnInit, OnDestroy {
  private readonly pdcService = inject(MauiPdcService);
  private readonly deviceMgmtService = inject(MauiDeviceMgmtService);
  private readonly dialogService = inject(MauiDialogService);
  private readonly cdr = inject(ChangeDetectorRef);

  private scanIntervalTimer?: ReturnType<typeof setInterval>;
  private slotSubscriptions: Map<number, Subscription> = new Map();

  isScanning = false;
  isBatchFlashing = false;
  isSubmittingLogs = false;
  logsSubmitted = false;
  fwChannel = 'stable';

  // 4 Device Slots
  deviceSlots: DeviceSlotState[] = [
    this.createEmptySlot(1),
    this.createEmptySlot(2),
    this.createEmptySlot(3),
    this.createEmptySlot(4),
  ];

  ngOnInit(): void {
    this.scanConnectedDevices();
    // Periodically poll for newly connected devices when not flashing
    this.scanIntervalTimer = setInterval(() => {
      if (!this.isBatchFlashing && !this.anySlotFlashing()) {
        this.scanConnectedDevices();
      }
    }, 3000);
  }

  ngOnDestroy(): void {
    if (this.scanIntervalTimer) {
      clearInterval(this.scanIntervalTimer);
    }
    this.slotSubscriptions.forEach(sub => sub.unsubscribe());
    this.slotSubscriptions.clear();
  }

  createEmptySlot(slotIndex: number): DeviceSlotState {
    return {
      slotIndex,
      serialNumber: '',
      devicePort: '',
      containerId: '',
      productDescription: '',
      isDetected: false,
      isFlashing: false,
      isPassed: false,
      isFailed: false,
      isBypassed: false,
      progressPercent: 0,
      currentBlock: '',
      statusMessage: 'Slot Empty - Connect Maui unit (PID 0x6002)',
      errorMessage: '',
      logContent: '',
    };
  }

  scanConnectedDevices(): void {
    this.isScanning = true;
    this.pdcService.discoverConnectedPdcDevices().subscribe({
      next: res => {
        this.isScanning = false;
        const discovered = res.devices || [];

        // Update slots 1 to 4
        for (let i = 0; i < 4; i++) {
          const slot = this.deviceSlots[i];
          if (slot.isFlashing) {
            continue; // Do not overwrite state of actively flashing slot
          }

          if (i < discovered.length) {
            const dev = discovered[i];
            const isSameDev =
              slot.serialNumber === dev.serialNumber &&
              slot.devicePort === dev.devicePort;
            slot.isDetected = true;
            slot.serialNumber = dev.serialNumber;
            slot.devicePort = dev.devicePort;
            slot.containerId = dev.containerId;
            slot.productDescription = dev.productDescription;
            if (!isSameDev) {
              // New device plugged in: reset slot state
              slot.isPassed = false;
              slot.isFailed = false;
              slot.isBypassed = false;
              slot.progressPercent = 0;
              slot.statusMessage = 'Device Ready for PDC update';
              slot.logContent = '';
            }
          } else {
            // Device unplugged: ALWAYS FREE THE SLOT so new unit can be plugged in!
            slot.isDetected = false;
            slot.isFlashing = false;
            slot.isPassed = false;
            slot.isFailed = false;
            slot.isBypassed = false;
            slot.progressPercent = 0;
            slot.serialNumber = '';
            slot.devicePort = '';
            slot.containerId = '';
            slot.productDescription = '';
            slot.statusMessage = 'Slot Empty - Connect Maui unit (PID 0x6002)';
            slot.logContent = '';
          }
        }
        this.cdr.detectChanges();
      },
      error: err => {
        this.isScanning = false;
        console.error('Device scan error:', err);
        this.cdr.detectChanges();
      },
    });
  }

  programAllDevices(): void {
    const detectedSlots = this.deviceSlots.filter(
      s => s.isDetected && !s.isFlashing,
    );
    if (detectedSlots.length === 0) {
      return;
    }

    this.isBatchFlashing = true;
    this.cdr.detectChanges();

    detectedSlots.forEach(slot => {
      this.programSingleSlot(slot);
    });
  }

  bypassSlot(slot: DeviceSlotState): void {
    slot.isFlashing = false;
    slot.isPassed = true;
    slot.isFailed = false;
    slot.isBypassed = true;
    slot.statusMessage = 'PDC Flash bypassed by operator.';
    this.checkBatchComplete();
    this.cdr.detectChanges();
  }

  programSingleSlot(slot: DeviceSlotState): void {
    if (!slot.isDetected || slot.isFlashing) {
      return;
    }

    slot.isFlashing = true;
    slot.isPassed = false;
    slot.isFailed = false;
    slot.isBypassed = false;
    slot.progressPercent = 0;
    slot.statusMessage = 'Starting PDC Flash...';
    slot.errorMessage = '';
    this.cdr.detectChanges();

    // Cancel any previous subscription for this slot
    this.slotSubscriptions.get(slot.slotIndex)?.unsubscribe();

    const sub = this.pdcService
      .programSinglePdc(
        slot.slotIndex,
        slot.serialNumber,
        slot.devicePort,
        this.fwChannel,
      )
      .subscribe({
        next: progress => {
          slot.progressPercent = progress.progressPercent;
          slot.currentBlock = progress.currentBlock;
          slot.statusMessage =
            progress.statusMessage ||
            `Flashing... ${progress.progressPercent}%`;

          if (progress.isComplete) {
            slot.isFlashing = false;
            if (progress.errorMessage) {
              slot.isFailed = true;
              slot.isPassed = false;
              slot.errorMessage = progress.errorMessage;
              slot.statusMessage = `PDC Flash Failed: ${progress.errorMessage}`;
              this.dialogService.openFailure(
                `Slot ${slot.slotIndex} PDC Flash Failed`,
                progress.errorMessage,
                () => this.programSingleSlot(slot),
                () => {
                  slot.isPassed = false;
                  slot.isFailed = true; // Latch failure
                  slot.isBypassed = true;
                  slot.statusMessage =
                    'PDC Flash Failed (Bypassed by operator).';
                  this.checkBatchComplete();
                  this.cdr.detectChanges();
                },
              );
            } else {
              slot.isPassed = true;
              slot.isFailed = false;
              slot.progressPercent = 100;
              slot.statusMessage = 'PDC Firmware Update Succeeded!';
            }
            this.checkBatchComplete();
          }
          this.cdr.detectChanges();
        },
        error: err => {
          slot.isFlashing = false;
          slot.isFailed = true;
          slot.isPassed = false;
          slot.errorMessage = err.message || 'PDC Flash Error';
          slot.statusMessage = `Error: ${err.message || 'Unknown error'}`;
          this.checkBatchComplete();
          this.dialogService.openFailure(
            `Slot ${slot.slotIndex} PDC Flash Error`,
            err,
            () => this.programSingleSlot(slot),
            () => {
              slot.isPassed = false;
              slot.isFailed = true; // Latch failure
              slot.isBypassed = true;
              slot.statusMessage = 'PDC Flash Failed (Bypassed by operator).';
              this.checkBatchComplete();
              this.cdr.detectChanges();
            },
          );
          this.cdr.detectChanges();
        },
      });

    this.slotSubscriptions.set(slot.slotIndex, sub);
  }

  saveLogs(): void {
    this.isSubmittingLogs = true;
    this.cdr.detectChanges();

    // Check if any devices are still physically connected before saving logs
    this.deviceMgmtService.readConnectedDeviceSerial().subscribe({
      next: res => {
        if (res.isConnected) {
          this.isSubmittingLogs = false;
          this.dialogService.openFailure(
            'Device Still Connected',
            `Maui unit '${res.serialNumber || 'device'}' is still detected on USB. Please physically unplug all flashed Maui boards from this test station before saving logs.`,
            () => this.saveLogs(),
          );
          this.cdr.detectChanges();
          return;
        }
        this.doSaveLogs();
      },
      error: () => {
        this.doSaveLogs();
      },
    });
  }

  private doSaveLogs(): void {
    const completedSlots = this.deviceSlots.filter(
      s => (s.isPassed || s.isBypassed || s.isFailed) && s.serialNumber,
    );
    if (completedSlots.length === 0) {
      this.isSubmittingLogs = false;
      this.logsSubmitted = true;
      this.cdr.detectChanges();
      return;
    }

    let savedCount = 0;
    completedSlots.forEach(slot => {
      let logText: string;
      if (slot.isFailed) {
        logText = `PDC Flash FAILED (Bypassed by operator). Slot ${slot.slotIndex}, Port: ${slot.devicePort}, Error: ${slot.errorMessage || slot.statusMessage}`;
      } else if (slot.isBypassed) {
        logText = `PDC Flash Bypassed by operator. Slot ${slot.slotIndex}, Port: ${slot.devicePort}`;
      } else {
        logText = `PDC Flash Verified Complete (100%). Slot ${slot.slotIndex}, Port: ${slot.devicePort}`;
      }

      this.deviceMgmtService
        .saveCardLog(slot.serialNumber, 'pdc_flashing_summary', logText)
        .subscribe({
          next: () => {
            savedCount++;
            if (savedCount >= completedSlots.length) {
              this.isSubmittingLogs = false;
              this.logsSubmitted = true;
              this.cdr.detectChanges();
            }
          },
          error: () => {
            savedCount++;
            if (savedCount >= completedSlots.length) {
              this.isSubmittingLogs = false;
              this.logsSubmitted = true;
              this.cdr.detectChanges();
            }
          },
        });
    });
  }

  checkBatchComplete(): void {
    if (!this.anySlotFlashing()) {
      this.isBatchFlashing = false;
    }
  }

  anySlotFlashing(): boolean {
    return this.deviceSlots.some(s => s.isFlashing);
  }

  hasDetectedDevices(): boolean {
    return this.deviceSlots.some(s => s.isDetected);
  }
}
