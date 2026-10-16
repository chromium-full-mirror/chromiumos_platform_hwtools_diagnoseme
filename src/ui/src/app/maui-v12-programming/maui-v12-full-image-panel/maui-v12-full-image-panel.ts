// Copyright 2026 The ChromiumOS Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

import {
  ChangeDetectorRef,
  Component,
  EventEmitter,
  Input,
  OnDestroy,
  OnInit,
  Output,
  inject,
} from '@angular/core';
import {CommonModule} from '@angular/common';
import {FormsModule} from '@angular/forms';
import {MatButtonModule} from '@angular/material/button';
import {MatCheckboxModule} from '@angular/material/checkbox';
import {MatFormFieldModule} from '@angular/material/form-field';
import {MatIconModule} from '@angular/material/icon';
import {MatProgressBarModule} from '@angular/material/progress-bar';
import {MatSelectModule} from '@angular/material/select';
import {Subscription} from 'rxjs';
import {MauiSerialProvisionCardComponent} from '../../maui-common/maui-serial-provision-card/maui-serial-provision-card';
import {MauiMcuService} from '../../services/maui-mcu.service';
import {
  MauiFirmwareFile,
  MauiMcuFullImageProgress,
  MauiUartBridge,
} from '../../../proto/maui-manufacturing.pb';

/** Firmware component of whole MCU images (MauiListFirmwareRequest). */
const FW_COMPONENT_MCU_FULL = 'mcu_full';
/** localStorage key of the bridge used last (by-id path or dev node). */
export const LAST_UART_PORT_KEY = 'diagnoseme.mauiV12.fullImage.uartPort';
/** The only baud rates itecomdbgr supports. */
export const FULL_IMAGE_BAUD_RATES = [115200, 3000000];
/** Lines kept in the live log. */
const MAX_LOG_LINES = 500;

const PHASE_LABELS: Record<string, string> = {
  connecting: 'Connecting to the MCU debugger',
  erasing: 'Erasing',
  checking: 'Blank check',
  programming: 'Programming',
  verifying: 'Verifying',
  rebooting: 'Waiting for the MCU App to enumerate',
  done: 'Done',
  failed: 'Failed',
};

/**
 * Maui v1.2 whole-image MCU flashing (itecomdbgr over a USB-UART bridge).
 *
 * Writes the full 1 MiB IT82002 flash through the MCU debug UART test
 * points. That erases the MCU serial number too, so the serial provisioning
 * card is offered after a successful flash. Leaving the page cancels the
 * RPC, which stops itecomdbgr.
 */
@Component({
  selector: 'app-maui-v12-full-image-panel',
  standalone: true,
  templateUrl: './maui-v12-full-image-panel.html',
  styleUrl: './maui-v12-full-image-panel.scss',
  imports: [
    CommonModule,
    FormsModule,
    MatButtonModule,
    MatCheckboxModule,
    MatFormFieldModule,
    MatIconModule,
    MatProgressBarModule,
    MatSelectModule,
    MauiSerialProvisionCardComponent,
  ],
})
export class MauiV12FullImagePanelComponent implements OnInit, OnDestroy {
  /** Set while another programming step uses the MCU (e.g. App update). */
  @Input() locked = false;
  /** Emits true while a flash runs, so the page can lock other steps. */
  @Output() readonly busyChange = new EventEmitter<boolean>();

  private readonly mcuService = inject(MauiMcuService);
  private readonly cdr = inject(ChangeDetectorRef);
  private bridgesSub?: Subscription;
  private imagesSub?: Subscription;
  private flashSub?: Subscription;

  readonly baudRates = FULL_IMAGE_BAUD_RATES;
  bridges: MauiUartBridge[] = [];
  bridgesMessage = '';
  scanning = false;
  selectedPort = '';
  images: MauiFirmwareFile[] = [];
  imagesMessage = '';
  selectedImage = '';
  verify = false;
  baud = FULL_IMAGE_BAUD_RATES[0];

  inProgress = false;
  progress = 0;
  phase = '';
  phasePercent = 0;
  status = '';
  result: 'NONE' | 'PASSED' | 'FAILED' = 'NONE';
  log: string[] = [];
  elapsedS = 0;
  mcuVersion = '';
  /** After a successful flash: the MCU serial number has to be restored. */
  showSerialCard = false;

  ngOnInit(): void {
    this.rescan();
    this.loadImages();
  }

  ngOnDestroy(): void {
    // Cancels a running flash: the server stops itecomdbgr.
    this.flashSub?.unsubscribe();
    this.bridgesSub?.unsubscribe();
    this.imagesSub?.unsubscribe();
  }

  get phaseLabel(): string {
    const label = PHASE_LABELS[this.phase] || this.phase;
    const toolPhase = ['erasing', 'checking', 'programming', 'verifying'];
    return toolPhase.includes(this.phase)
      ? `${label} ${this.phasePercent}%`
      : label;
  }

  get elapsedText(): string {
    const seconds = Math.floor(this.elapsedS);
    return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`;
  }

  get selectedImageFile(): MauiFirmwareFile | undefined {
    return this.images.find(f => f.fileName === this.selectedImage);
  }

  get canStart(): boolean {
    const image = this.selectedImageFile;
    return (
      !this.inProgress &&
      !this.locked &&
      !!this.selectedPort &&
      !!image &&
      this.isValidImage(image)
    );
  }

  isValidImage(file: MauiFirmwareFile): boolean {
    const expected = Number(file.expectedSizeBytes || 0);
    return !expected || Number(file.sizeBytes) === expected;
  }

  bridgeLabel(bridge: MauiUartBridge): string {
    const hex = (n: number) => n.toString(16).padStart(4, '0');
    const name = bridge.byId
      ? bridge.byId.split('/').pop()
      : [bridge.manufacturer, bridge.product].filter(Boolean).join(' ') ||
        `${hex(bridge.vid)}:${hex(bridge.pid)}`;
    return `${name} (${bridge.devicePort})`;
  }

  rescan(): void {
    this.bridgesSub?.unsubscribe();
    this.scanning = true;
    this.bridgesSub = this.mcuService.listUartBridges().subscribe({
      next: res => {
        this.scanning = false;
        this.bridges = res.bridges || [];
        this.bridgesMessage = res.message;
        this.selectedPort = this.pickPort(res.defaultPort);
        this.cdr.detectChanges();
      },
      error: err => {
        this.scanning = false;
        this.bridges = [];
        this.selectedPort = '';
        this.bridgesMessage = `USB-UART bridge scan failed: ${err?.message || err}`;
        this.cdr.detectChanges();
      },
    });
  }

  loadImages(): void {
    this.imagesSub?.unsubscribe();
    this.imagesSub = this.mcuService
      .listFirmware(FW_COMPONENT_MCU_FULL)
      .subscribe({
        next: res => {
          this.images = res.files || [];
          this.imagesMessage = res.message;
          const current = this.selectedImageFile;
          if (!current || !this.isValidImage(current)) {
            // The stable image (STABLE.json) if it has the right size, else
            // the newest one that has (the list is newest first).
            const valid = this.images.filter(f => this.isValidImage(f));
            this.selectedImage =
              (valid.find(f => f.isStable) || valid[0])?.fileName || '';
          }
          this.cdr.detectChanges();
        },
        error: err => {
          this.images = [];
          this.selectedImage = '';
          this.imagesMessage = `Firmware listing failed: ${err?.message || err}`;
          this.cdr.detectChanges();
        },
      });
  }

  start(): void {
    if (!this.canStart) {
      return;
    }
    this.remember(this.bridges.find(b => b.devicePort === this.selectedPort));
    this.setInProgress(true);
    this.progress = 0;
    this.phase = 'connecting';
    this.phasePercent = 0;
    this.status = 'Starting itecomdbgr...';
    this.result = 'NONE';
    this.log = [];
    this.elapsedS = 0;
    this.mcuVersion = '';
    this.showSerialCard = false;
    this.flashSub?.unsubscribe();
    this.flashSub = this.mcuService
      .updateMcuFullImage(
        this.selectedPort,
        this.selectedImage,
        this.verify,
        this.baud,
      )
      .subscribe({
        next: p => this.onProgress(p),
        error: err => {
          this.setInProgress(false);
          this.result = 'FAILED';
          this.status = `MCU full image flash error: ${err?.message || err}`;
          this.cdr.detectChanges();
        },
        complete: () => {
          this.setInProgress(false);
          this.cdr.detectChanges();
        },
      });
  }

  private onProgress(p: MauiMcuFullImageProgress): void {
    if (p.phase) {
      this.phase = p.phase;
    }
    this.phasePercent = p.phasePercent || 0;
    if (p.isComplete || p.progressPercent > this.progress) {
      this.progress = p.progressPercent;
    }
    if (p.statusMessage) {
      this.status = p.statusMessage;
    }
    if (p.logLine) {
      this.log.push(p.logLine);
      if (this.log.length > MAX_LOG_LINES) {
        this.log.splice(0, this.log.length - MAX_LOG_LINES);
      }
    }
    this.elapsedS = p.elapsedS || this.elapsedS;
    if (p.isComplete) {
      this.setInProgress(false);
      this.result = p.success ? 'PASSED' : 'FAILED';
      this.mcuVersion = p.mcuVersion;
      this.showSerialCard = p.success;
      if (!p.success) {
        this.status = p.errorMessage || p.statusMessage;
      }
    }
    this.cdr.detectChanges();
  }

  private setInProgress(inProgress: boolean): void {
    if (this.inProgress !== inProgress) {
      this.inProgress = inProgress;
      this.busyChange.emit(inProgress);
    }
  }

  /** Keeps the current choice, else the last used bridge, else the default. */
  private pickPort(defaultPort: string): string {
    let remembered = '';
    try {
      remembered = localStorage.getItem(LAST_UART_PORT_KEY) || '';
    } catch {
      // Storage disabled: fall back to the server default.
    }
    const find = (port: string) =>
      port
        ? this.bridges.find(b => b.devicePort === port || b.byId === port)
        : undefined;
    const bridge =
      find(this.selectedPort) ||
      find(remembered) ||
      find(defaultPort) ||
      this.bridges[0];
    return bridge?.devicePort || '';
  }

  private remember(bridge?: MauiUartBridge): void {
    if (!bridge) {
      return;
    }
    try {
      localStorage.setItem(
        LAST_UART_PORT_KEY,
        bridge.byId || bridge.devicePort,
      );
    } catch {
      // Storage disabled: the server default is used next time.
    }
  }
}
