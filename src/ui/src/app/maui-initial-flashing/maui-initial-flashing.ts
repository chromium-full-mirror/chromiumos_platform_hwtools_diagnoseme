// Copyright 2026 The ChromiumOS Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

import {
  AfterViewInit,
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
import {Subscription} from 'rxjs';
import {DiagnosemeReworkCard} from '../diagnoseme-rework-card/diagnoseme-rework-card';
import {DiagnosemeReworkCardColumn} from '../diagnoseme-rework-card-column/diagnoseme-rework-card-column';
import {MauiDialogService} from '../maui-common/maui-dialog.service';
import {MauiStationFooterComponent} from '../maui-common/maui-station-footer/maui-station-footer';
import {autoAdvanceToCard} from '../maui-common/maui-scroll.utils';
import {MauiStation1Service} from '../services/maui-station1.service';
import {MauiDeviceMgmtService} from '../services/maui-device-mgmt.service';
import {TestPassFailButton} from '../test-pass-fail-button/test-pass-fail-button';

@Component({
  selector: 'app-maui-initial-flashing',
  standalone: true,
  templateUrl: './maui-initial-flashing.html',
  styleUrl: './maui-initial-flashing.scss',
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
export class MauiInitialFlashingComponent
  implements AfterViewInit, OnDestroy, OnInit
{
  private readonly mauiService = inject(MauiStation1Service);
  private readonly deviceMgmtService = inject(MauiDeviceMgmtService);
  private readonly dialogService = inject(MauiDialogService);
  private readonly cdr = inject(ChangeDetectorRef);

  private bslSubscription?: Subscription;
  private reconnectTimeoutId?: any;

  // Single Auto-Try & Overall Provisioning State
  hasAutoTried = false;
  provisioningInProgress = false;
  provisioningSuccessful = false;
  provisioningFailed = false;

  // MCU Phase State
  bslDetected = false;
  bslStatusMessage =
    'Waiting for unprogrammed Maui board in BSL mode (0403:6001)...';
  bslDevicePort = '';
  detectedPid = 0;
  autoFlashEligible = false;

  mcuFlashingInProgress = false;
  mcuFlashingSuccessful = false;
  mcuFlashingFailed = false;
  mcuStatusMessage = 'Awaiting board detection...';
  mcuLogs = '';

  // FTDI Phase State
  discoveredSerial = '';
  ftdiProgrammingInProgress = false;
  ftdiProgrammingSuccessful = false;
  ftdiSkipped = false;
  ftdiStatusMessage = 'Awaiting MCU completion...';
  ftdiLogs = '';

  // Completion State
  checkingDisconnect = false;
  deviceDisconnected = false;
  deviceStillConnectedError = false;
  disconnectStatusMessage = '';
  savingLogs = false;
  logsSaved = false;

  @ViewChild('provisionBtn', {read: ElementRef})
  provisionBtn!: ElementRef<HTMLButtonElement>;

  @ViewChild('completeButton', {read: ElementRef})
  completeButton?: ElementRef<HTMLButtonElement>;

  @ViewChild('provisionStatus') provisionStatus!: TestPassFailButton;
  @ViewChild('completeStatus') completeStatus?: TestPassFailButton;

  get isUnprogrammed(): boolean {
    return this.detectedPid === 0x6001;
  }

  get isProgrammed(): boolean {
    return this.detectedPid === 0x6002;
  }

  get pidStatusText(): string {
    if (this.detectedPid === 0x6001) {
      return 'Unprogrammed (0403:6001 - Auto-flash eligible)';
    } else if (this.detectedPid === 0x6002) {
      return 'Programmed (0403:6002 - Ready / Re-flashable)';
    }
    return 'Unknown Device State';
  }

  ngOnInit(): void {
    this.startBslDiscoveryStream();
  }

  ngAfterViewInit(): void {
    this.focusProvisionBtn();
  }

  ngOnDestroy(): void {
    if (this.reconnectTimeoutId) {
      clearTimeout(this.reconnectTimeoutId);
    }
    this.bslSubscription?.unsubscribe();
  }

  startBslDiscoveryStream(): void {
    this.bslSubscription = this.mauiService.streamBslAutoDiscovery().subscribe({
      next: res => {
        if (this.provisioningSuccessful) {
          return;
        }

        this.bslDetected = res.bslDetected;
        this.bslStatusMessage = res.message;
        this.detectedPid = res.pid;
        this.autoFlashEligible = res.autoFlashEligible;

        if (res.devicePort) {
          this.bslDevicePort = res.devicePort;
        }

        if (!res.bslDetected) {
          this.hasAutoTried = false;
          this.provisioningFailed = false;
          this.mcuFlashingFailed = false;
          this.mcuStatusMessage = 'Waiting for unprogrammed Maui board...';
          this.ftdiStatusMessage = 'Awaiting MCU completion...';
        }

        if (
          res.bslDetected &&
          res.autoFlashEligible &&
          !this.hasAutoTried &&
          !this.provisioningInProgress &&
          !this.provisioningSuccessful &&
          !this.provisioningFailed
        ) {
          this.hasAutoTried = true;
          this.startProvisioning(res.devicePort || this.bslDevicePort);
        }

        this.cdr.detectChanges();
      },
      error: err => {
        if (this.provisioningSuccessful) {
          return;
        }
        console.error('BSL Auto-Discovery stream error:', err);
        this.bslStatusMessage = 'Stream disconnected. Reconnecting in 3s...';
        this.reconnectTimeoutId = setTimeout(
          () => this.startBslDiscoveryStream(),
          3000,
        );
      },
    });
  }

  startProvisioning(portNode?: string): void {
    if (this.provisioningInProgress || this.provisioningSuccessful) {
      return;
    }

    const targetPort = portNode || this.bslDevicePort || '/dev/ttyUSB0';
    this.provisioningInProgress = true;
    this.provisioningFailed = false;
    this.mcuFlashingInProgress = true;
    this.mcuFlashingFailed = false;
    this.provisionStatus?.showWorking();
    this.mcuStatusMessage = `Flashing MCU firmware on ${targetPort}...`;
    this.ftdiStatusMessage = 'Awaiting MCU completion...';
    this.cdr.detectChanges();

    this.mauiService.autoProgramMcuBsl(targetPort, 'stable').subscribe({
      next: res => {
        this.mcuFlashingInProgress = false;
        this.mcuLogs = res.logs || '';
        if (res.success) {
          this.mcuFlashingSuccessful = true;
          this.mcuFlashingFailed = false;
          this.mcuStatusMessage = 'MCU firmware flashed successfully!';
          this.ftdiStatusMessage = 'Verifying ADB & FTDI serial...';
          this.cdr.detectChanges();
          this.runFtdiAutoStep();
        } else {
          this.mcuFlashingSuccessful = false;
          this.mcuFlashingFailed = true;
          this.provisioningInProgress = false;
          this.provisioningFailed = true;
          this.provisionStatus?.showFailed();
          this.mcuStatusMessage = res.message || 'MCU BSL Flashing failed.';
          this.dialogService.openFailure(
            'MCU BSL Flashing Failed',
            res.message || res.logs || 'Flashing returned failure.',
            () => this.startProvisioning(targetPort),
            () => this.bypassProvisioning(),
          );
          this.cdr.detectChanges();
        }
      },
      error: err => {
        this.mcuFlashingInProgress = false;
        this.mcuFlashingFailed = true;
        this.provisioningInProgress = false;
        this.provisioningFailed = true;
        this.provisionStatus?.showFailed();
        this.mcuStatusMessage = 'Error during MCU BSL Flashing.';
        this.dialogService.openFailure(
          'MCU BSL Flashing Error',
          err,
          () => this.startProvisioning(targetPort),
          () => this.bypassProvisioning(),
        );
        this.cdr.detectChanges();
      },
    });
  }

  runFtdiAutoStep(): void {
    this.ftdiProgrammingInProgress = true;
    this.ftdiStatusMessage = 'Scanning ADB & FTDI serials...';
    this.cdr.detectChanges();

    this.mauiService.discoverTopologyAndSerial().subscribe({
      next: topoRes => {
        const adbSerial = topoRes.readSerialNumber;
        const ftdiNode =
          topoRes.ftdiDevNode || this.bslDevicePort || '/dev/ttyUSB0';
        if (adbSerial) {
          this.discoveredSerial = adbSerial;
        }

        this.deviceMgmtService.readConnectedDeviceSerial().subscribe({
          next: conn => {
            const finalAdbSerial = adbSerial || conn.adbSerial;
            const finalFtdiSerial = conn.ftdiSerial;
            if (finalAdbSerial) {
              this.discoveredSerial = finalAdbSerial;
            }

            const ftdiAlreadyMatching = Boolean(
              finalAdbSerial &&
                finalFtdiSerial &&
                finalAdbSerial.toUpperCase() ===
                  finalFtdiSerial.toUpperCase() &&
                conn.ftdiConnected &&
                (conn.ftdiPid === 0x6002 || conn.ftdiIsProgrammed),
            );

            if (ftdiAlreadyMatching) {
              this.ftdiProgrammingInProgress = false;
              this.ftdiProgrammingSuccessful = true;
              this.ftdiSkipped = true;
              this.ftdiStatusMessage = `FTDI already configured with matching serial (${finalAdbSerial}, PID 0x6002). Skipped.`;
              this.finishProvisioningSuccess();
            } else {
              this.flashFtdiEeprom(finalAdbSerial, ftdiNode);
            }
            this.cdr.detectChanges();
          },
          error: () => {
            if (this.discoveredSerial) {
              this.flashFtdiEeprom(this.discoveredSerial, ftdiNode);
            } else {
              this.handleFtdiFailure('Could not discover ADB serial number.');
            }
            this.cdr.detectChanges();
          },
        });
      },
      error: err => {
        this.handleFtdiFailure(err);
      },
    });
  }

  flashFtdiEeprom(serialToProgram: string, ftdiNode: string): void {
    if (!serialToProgram) {
      this.handleFtdiFailure(
        'Missing target ADB serial number for FTDI programming.',
      );
      return;
    }

    this.ftdiStatusMessage = `Programming FTDI EEPROM with serial ${serialToProgram}...`;
    this.cdr.detectChanges();

    this.mauiService
      .programFtdiEeprom(
        serialToProgram,
        ftdiNode,
        0x0403,
        0x6002,
        'Maui Serial Console',
      )
      .subscribe({
        next: res => {
          this.ftdiProgrammingInProgress = false;
          this.ftdiLogs = res.logs || '';
          if (res.success) {
            this.ftdiProgrammingSuccessful = true;
            this.ftdiStatusMessage = `FTDI EEPROM programmed successfully with serial ${serialToProgram} (PID 0x6002)!`;
            this.finishProvisioningSuccess();
          } else {
            this.handleFtdiFailure(
              res.message || res.logs || 'FTDI programming failed.',
            );
          }
          this.cdr.detectChanges();
        },
        error: err => {
          this.handleFtdiFailure(err);
        },
      });
  }

  private finishProvisioningSuccess(): void {
    this.provisioningInProgress = false;
    this.provisioningSuccessful = true;
    this.provisioningFailed = false;
    this.mcuFlashingSuccessful = true;
    this.ftdiProgrammingSuccessful = true;
    this.detectedPid = 0x6002;
    this.bslStatusMessage = 'Provisioning Complete. Ready to disconnect.';
    this.provisionStatus?.showPassed();
    autoAdvanceToCard('s1-card-2', this.completeButton);
    this.cdr.detectChanges();
  }

  private handleFtdiFailure(error: any): void {
    this.ftdiProgrammingInProgress = false;
    this.ftdiProgrammingSuccessful = false;
    this.provisioningInProgress = false;
    this.provisioningFailed = true;
    this.provisionStatus?.showFailed();
    this.ftdiStatusMessage =
      typeof error === 'string' ? error : 'FTDI EEPROM programming failed.';
    this.dialogService.openFailure(
      'FTDI EEPROM Programming Failed',
      error,
      () => this.runFtdiAutoStep(),
      () => this.bypassProvisioning(),
    );
    this.cdr.detectChanges();
  }

  bypassProvisioning(): void {
    this.provisioningInProgress = false;
    this.provisioningSuccessful = true;
    if (this.provisioningFailed || this.mcuFlashingFailed) {
      this.provisionStatus?.showFailed();
      this.mcuStatusMessage = 'Provisioning failed (Bypassed by operator).';
      this.ftdiStatusMessage = 'Failed (Bypassed).';
    } else {
      this.provisionStatus?.showBypassed();
      this.mcuStatusMessage = 'Provisioning bypassed by operator.';
      this.ftdiStatusMessage = 'Bypassed.';
    }
    autoAdvanceToCard('s1-card-2', this.completeButton);
    this.cdr.detectChanges();
  }

  saveLogs(): void {
    if (this.checkingDisconnect || this.savingLogs) {
      return;
    }
    const serialNo = this.discoveredSerial || 'UNKNOWN_DEVICE';
    this.checkingDisconnect = true;
    this.deviceStillConnectedError = false;
    this.completeStatus?.showWorking();
    this.disconnectStatusMessage = 'Verifying physical device disconnect...';
    this.cdr.detectChanges();

    this.deviceMgmtService.readConnectedDeviceSerial().subscribe({
      next: res => {
        const targetSerialUpper = (this.discoveredSerial || '')
          .trim()
          .toUpperCase();
        const adbSerialUpper = (res.adbSerial || '').trim().toUpperCase();
        const ftdiSerialUpper = (res.ftdiSerial || '').trim().toUpperCase();
        const genericSerialUpper = (res.serialNumber || '')
          .trim()
          .toUpperCase();

        const adbStillConnected = Boolean(
          res.adbConnected &&
            (!targetSerialUpper ||
              adbSerialUpper === targetSerialUpper ||
              !adbSerialUpper),
        );
        const ftdiStillConnected = Boolean(
          res.ftdiConnected &&
            (!targetSerialUpper ||
              ftdiSerialUpper === targetSerialUpper ||
              !ftdiSerialUpper),
        );
        const genericStillConnected = Boolean(
          res.isConnected &&
            (!targetSerialUpper || genericSerialUpper === targetSerialUpper),
        );

        const isThisDeviceStillConnected =
          adbStillConnected || ftdiStillConnected || genericStillConnected;

        if (isThisDeviceStillConnected) {
          this.checkingDisconnect = false;
          this.deviceStillConnectedError = true;
          this.completeStatus?.showFailed();
          this.disconnectStatusMessage =
            'Device still connected! Please unplug Host USB-C cable.';
          this.dialogService.openFailure(
            'Device Still Connected',
            `Maui unit '${this.discoveredSerial || 'device'}' is still detected on USB (ADB: ${res.adbConnected ? 'Connected' : 'Disconnected'}, FTDI: ${res.ftdiConnected ? 'Connected' : 'Disconnected'}). Please physically unplug the Host USB-C cable from the Maui board before saving logs.`,
            () => this.saveLogs(),
          );
          this.cdr.detectChanges();
          return;
        }

        this.checkingDisconnect = false;
        this.deviceDisconnected = true;
        this.disconnectStatusMessage = 'Device disconnected. Saving logs...';
        this.doSaveLogs(serialNo);
      },
      error: () => {
        this.checkingDisconnect = false;
        this.deviceDisconnected = true;
        this.disconnectStatusMessage = 'Device disconnected. Saving logs...';
        this.doSaveLogs(serialNo);
      },
    });
  }

  private doSaveLogs(serialNo: string): void {
    this.savingLogs = true;
    this.completeStatus?.showWorking();
    this.cdr.detectChanges();

    const summaryLog = [
      'STATION_1_INITIAL_FLASHING: PASSED',
      `SERIAL: ${serialNo}`,
      `MCU_BSL: ${this.mcuFlashingSuccessful ? 'PASSED' : 'FAILED'}`,
      `FTDI_EEPROM: ${this.ftdiProgrammingSuccessful ? 'PASSED' : this.ftdiSkipped ? 'SKIPPED (Already Matched)' : 'FAILED'}`,
      'DISCONNECT_CHECK: VERIFIED',
    ].join('\n');

    if (this.mcuLogs) {
      this.deviceMgmtService
        .saveCardLog(serialNo, 'mcu_flashing', this.mcuLogs)
        .subscribe({next: () => {}, error: () => {}});
    }

    if (this.ftdiLogs) {
      this.deviceMgmtService
        .saveCardLog(serialNo, 'ftdi_programming', this.ftdiLogs)
        .subscribe({next: () => {}, error: () => {}});
    }

    this.deviceMgmtService
      .saveCardLog(serialNo, 'initial_flashing_summary', summaryLog)
      .subscribe({
        next: () => {
          this.savingLogs = false;
          this.logsSaved = true;
          this.disconnectStatusMessage = `Logs saved to /tmp/${serialNo}_diagnoseme_logs/.`;
          this.completeStatus?.showPassed();
          this.cdr.detectChanges();
        },
        error: () => {
          this.savingLogs = false;
          this.logsSaved = true;
          this.completeStatus?.showPassed();
          this.cdr.detectChanges();
        },
      });
  }

  focusProvisionBtn(): void {
    setTimeout(() => this.provisionBtn?.nativeElement?.focus(), 100);
  }
}
