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
import {FormsModule} from '@angular/forms';
import {MatButtonModule} from '@angular/material/button';
import {MatExpansionModule} from '@angular/material/expansion';
import {MatFormFieldModule} from '@angular/material/form-field';
import {MatIconModule} from '@angular/material/icon';
import {MatProgressBarModule} from '@angular/material/progress-bar';
import {MatSelectModule} from '@angular/material/select';
import {Subscription} from 'rxjs';
import {MauiPdcFlashPanelComponent} from '../maui-common/maui-pdc-flash-panel/maui-pdc-flash-panel';
import {MauiMcuService} from '../services/maui-mcu.service';
import {
  MauiFirmwareFile,
  MauiMcuConsoleInfo,
} from '../../proto/maui-manufacturing.pb';
import {MauiV12FullImagePanelComponent} from './maui-v12-full-image-panel/maui-v12-full-image-panel';

/** Component names of MauiListFirmwareRequest. */
const FW_COMPONENT_MCU_APP = 'mcu_app';
/** Size of a full MCU image, which must never go to the App update. */
const MCU_FULL_IMAGE_SIZE = 1024 * 1024;

/** True for files the App update may take (the server filters too). */
export function isAppImage(file: MauiFirmwareFile): boolean {
  return (
    Number(file.sizeBytes) !== MCU_FULL_IMAGE_SIZE &&
    !/full_flash|bootloader/i.test(file.fileName)
  );
}

/**
 * Maui v1.2 programming (help) page.
 *
 * Not a guided test flow: the operator picks what to program.
 *  - MCU App: RW bootloader update (maui-flash mcu --target app).
 *  - MCU full image: whole flash with itecomdbgr over a USB-UART bridge.
 *  - PDC: TPS66994 firmware over the MCU console (same as v1.1).
 */
@Component({
  selector: 'app-maui-v12-programming',
  standalone: true,
  templateUrl: './maui-v12-programming.html',
  styleUrl: './maui-v12-programming.scss',
  imports: [
    CommonModule,
    FormsModule,
    MatButtonModule,
    MatExpansionModule,
    MatFormFieldModule,
    MatIconModule,
    MatProgressBarModule,
    MatSelectModule,
    MauiPdcFlashPanelComponent,
    MauiV12FullImagePanelComponent,
  ],
})
export class MauiV12ProgrammingComponent implements OnInit, OnDestroy {
  private readonly mcuService = inject(MauiMcuService);
  private readonly cdr = inject(ChangeDetectorRef);
  private readonly subs = new Subscription();
  private refreshSubs?: Subscription;

  consoles: MauiMcuConsoleInfo[] = [];
  selectedPort = '';
  firmwareFiles: MauiFirmwareFile[] = [];
  selectedFile = '';
  firmwareMessage = '';

  appUpdateInProgress = false;
  appProgress = 0;
  appStatus = '';
  appResult: 'NONE' | 'PASSED' | 'FAILED' = 'NONE';
  appLog: string[] = [];
  /** A full image flash runs: the MCU is halted, no App update. */
  fullImageBusy = false;

  ngOnInit(): void {
    this.refresh();
  }

  ngOnDestroy(): void {
    this.refreshSubs?.unsubscribe();
    this.subs.unsubscribe();
  }

  refresh(): void {
    // Cancel a refresh that is still running (repeated clicks).
    this.refreshSubs?.unsubscribe();
    this.refreshSubs = new Subscription();
    const statusSub = this.mcuService.getUnitStatus().subscribe({
      next: res => {
        this.consoles = res.consoles || [];
        if (!this.consoles.some(c => c.devicePort === this.selectedPort)) {
          this.selectedPort = res.mcu?.devicePort || '';
        }
        this.cdr.detectChanges();
      },
      error: () => {
        this.consoles = [];
        this.cdr.detectChanges();
      },
    });
    this.refreshSubs.add(statusSub);
    const firmwareSub = this.mcuService
      .listFirmware(FW_COMPONENT_MCU_APP)
      .subscribe({
        next: res => {
          this.firmwareFiles = (res.files || []).filter(isAppImage);
          this.firmwareMessage = res.message;
          if (!this.firmwareFiles.some(f => f.fileName === this.selectedFile)) {
            // The stable App image (STABLE.json), else the newest one.
            this.selectedFile =
              (
                this.firmwareFiles.find(f => f.isStable) ||
                this.firmwareFiles[0]
              )?.fileName || '';
          }
          this.cdr.detectChanges();
        },
        error: err => {
          this.firmwareMessage = `Firmware listing failed: ${err?.message || err}`;
          this.cdr.detectChanges();
        },
      });
    this.refreshSubs.add(firmwareSub);
  }

  updateMcuApp(): void {
    if (!this.selectedPort || !this.selectedFile || this.fullImageBusy) {
      return;
    }
    this.appUpdateInProgress = true;
    this.appProgress = 0;
    this.appResult = 'NONE';
    this.appLog = [];
    this.appStatus = 'Starting MCU App update...';
    this.subs.add(
      this.mcuService
        // No serial: the server logs under the serial of the selected
        // console's own board (the unit status only knows the first board).
        .updateMcuApp(this.selectedPort, '', this.selectedFile)
        .subscribe({
          next: p => {
            if (p.progressPercent) {
              this.appProgress = p.progressPercent;
            }
            if (p.statusMessage) {
              this.appStatus = p.statusMessage;
            }
            if (p.logLine) {
              this.appLog.push(p.logLine);
            }
            if (p.isComplete) {
              this.appUpdateInProgress = false;
              this.appResult = p.success ? 'PASSED' : 'FAILED';
              if (!p.success) {
                this.appStatus = p.errorMessage || p.statusMessage;
              }
              this.refresh();
            }
            this.cdr.detectChanges();
          },
          error: err => {
            this.appUpdateInProgress = false;
            this.appResult = 'FAILED';
            this.appStatus = `MCU App update error: ${err?.message || err}`;
            this.cdr.detectChanges();
          },
          complete: () => {
            this.appUpdateInProgress = false;
            this.cdr.detectChanges();
          },
        }),
    );
  }
}
