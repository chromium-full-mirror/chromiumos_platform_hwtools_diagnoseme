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
import {MatInputModule} from '@angular/material/input';
import {MatProgressBarModule} from '@angular/material/progress-bar';
import {MatRadioModule} from '@angular/material/radio';
import {MatSelectModule} from '@angular/material/select';
import {Subscription} from 'rxjs';
import {MauiSerialProvisionCardComponent} from '../../maui-common/maui-serial-provision-card/maui-serial-provision-card';
import {MauiMcuService} from '../../services/maui-mcu.service';
import {
  MauiH2hFirmwareFile,
  MauiH2hFlashProgress,
  MauiMcuConsoleInfo,
} from '../../../proto/maui-manufacturing.pb';

/** Maui v1.2 unit serial (maui_v12_profile.SERIAL_RE). */
export const MAUI_V12_SERIAL_RE = /^MAUIV12H\d{8}$/;
/** Characters of the sha256 shown in the file list. */
export const SHA256_SHORT_CHARS = 12;
/** Lines kept in the live log. */
const MAX_LOG_LINES = 500;

const PHASE_LABELS: Record<string, string> = {
  checking: 'Checking the MCU App and the H2H SPI bus',
  erasing: 'Erasing',
  programming: 'Programming',
  verifying: 'Verifying (CRC-32)',
  reading: 'Reading back',
  rebooting: 'Resetting the H2H bridge, checking its serial number',
  done: 'Done',
  failed: 'Failed',
};
const TOOL_PHASES = ['erasing', 'programming', 'verifying', 'reading'];

/**
 * Maui v1.2 H2H bridge (Prolific PL27A1) firmware flashing.
 *
 * The image is not part of DiagnoseMe: the operator copies it to the H2H
 * firmware directory of the station, which the server lists. The server
 * flashes it with maui-flash h2h (maui-utils), optionally with another serial
 * number patched into a copy, then resets the bridge and checks its USB
 * iSerial. Leaving the page cancels the RPC, which stops maui-flash.
 */
@Component({
  selector: 'app-maui-v12-h2h-flash-panel',
  standalone: true,
  templateUrl: './maui-v12-h2h-flash-panel.html',
  styleUrl: './maui-v12-h2h-flash-panel.scss',
  imports: [
    CommonModule,
    FormsModule,
    MatButtonModule,
    MatCheckboxModule,
    MatFormFieldModule,
    MatIconModule,
    MatInputModule,
    MatProgressBarModule,
    MatRadioModule,
    MatSelectModule,
    MauiSerialProvisionCardComponent,
  ],
})
export class MauiV12H2hFlashPanelComponent implements OnInit, OnDestroy {
  /** Set while another programming step uses the MCU. */
  @Input() locked = false;
  /** Emits true while a flash runs, so the page can lock other steps. */
  @Output() readonly busyChange = new EventEmitter<boolean>();

  private readonly mcuService = inject(MauiMcuService);
  private readonly cdr = inject(ChangeDetectorRef);
  private listSub?: Subscription;
  private flashSub?: Subscription;
  private consoleList: MauiMcuConsoleInfo[] = [];
  private initialized = false;

  directory = '';
  directoryExists = true;
  files: MauiH2hFirmwareFile[] = [];
  listMessage = '';
  loading = false;
  selectedFile = '';
  selectedPort = '';
  changeSerial = false;
  newSerial = '';
  readBack = true;
  /** MCU App of the selected console compared with the stable build. */
  mcuVersion = '';
  firmwareVerdict = '';
  firmwareMessage = '';
  firmwareWarning = false;

  inProgress = false;
  progress = 0;
  phase = '';
  phasePercent = 0;
  status = '';
  result: 'NONE' | 'PASSED' | 'FAILED' = 'NONE';
  log: string[] = [];
  elapsedS = 0;
  hostSerial = '';
  /** After a flash that changed the H2H serial: provision the MCU serial. */
  showSerialCard = false;

  /** MCU consoles of the unit status (the App console is needed). */
  @Input()
  set consoles(consoles: MauiMcuConsoleInfo[] | null | undefined) {
    this.consoleList = consoles || [];
    const apps = this.appConsoles;
    if (!apps.some(c => c.devicePort === this.selectedPort)) {
      this.selectedPort = apps[0]?.devicePort || '';
      if (this.initialized && !this.inProgress) {
        // Check the MCU App of the new console (outside this input update).
        void Promise.resolve().then(() => this.refresh());
      }
    }
  }

  get consoles(): MauiMcuConsoleInfo[] {
    return this.consoleList;
  }

  get appConsoles(): MauiMcuConsoleInfo[] {
    return this.consoleList.filter(c => c.stage === 'app');
  }

  ngOnInit(): void {
    this.initialized = true;
    this.refresh();
  }

  ngOnDestroy(): void {
    // Cancels a running flash: the server stops maui-flash.
    this.flashSub?.unsubscribe();
    this.listSub?.unsubscribe();
  }

  get selected(): MauiH2hFirmwareFile | undefined {
    return this.files.find(f => f.fileName === this.selectedFile);
  }

  get validFiles(): MauiH2hFirmwareFile[] {
    return this.files.filter(f => f.valid);
  }

  /** The serial number to write, normalized ('' to keep the image's). */
  get serialToWrite(): string {
    return this.changeSerial ? this.newSerial.trim().toUpperCase() : '';
  }

  /** Why the serial number input cannot be used, '' if it can. */
  get serialError(): string {
    if (!this.changeSerial) {
      return '';
    }
    const file = this.selected;
    if (file && !file.serialPatchable) {
      return `The serial number of ${file.fileName} cannot be changed safely: ${file.serialPatchError}`;
    }
    if (!MAUI_V12_SERIAL_RE.test(this.serialToWrite)) {
      return 'Enter MAUIV12H followed by 8 digits, e.g. MAUIV12H26010001.';
    }
    return '';
  }

  /** Why the flash cannot start, '' if it can. */
  get blockReason(): string {
    const file = this.selected;
    if (!file) {
      return this.validFiles.length
        ? 'Select the H2H image to flash.'
        : 'No valid H2H image to flash.';
    }
    if (!file.valid) {
      return `${file.fileName}: ${file.error}`;
    }
    if (!this.selectedPort) {
      return 'No Maui MCU App console (18d1:5085) detected.';
    }
    if (this.serialError) {
      return this.serialError;
    }
    if (!this.changeSerial && !file.serialValid) {
      return (
        `The serial number '${file.serialNumber}' of ${file.fileName} is not ` +
        "a valid Maui v1.2 serial: use 'Change serial number'."
      );
    }
    return '';
  }

  get canStart(): boolean {
    return !this.inProgress && !this.locked && !this.blockReason;
  }

  get phaseLabel(): string {
    const label = PHASE_LABELS[this.phase] || this.phase;
    return TOOL_PHASES.includes(this.phase)
      ? `${label} ${this.phasePercent}%`
      : label;
  }

  get elapsedText(): string {
    const seconds = Math.floor(this.elapsedS);
    return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`;
  }

  shortSha(sha: string): string {
    return (sha || '').slice(0, SHA256_SHORT_CHARS);
  }

  refresh(): void {
    this.listSub?.unsubscribe();
    this.loading = true;
    this.listSub = this.mcuService
      .listH2hFirmware(this.selectedPort)
      .subscribe({
        next: res => {
          this.loading = false;
          this.mcuVersion = res.mcuVersion;
          this.firmwareVerdict = res.firmwareVerdict;
          this.firmwareMessage = res.firmwareMessage;
          this.firmwareWarning = res.firmwareWarning;
          this.directory = res.directory;
          this.directoryExists = res.directoryExists;
          this.files = res.files || [];
          this.listMessage = res.message;
          const current = this.selected;
          if (!current || !current.valid) {
            // Only a single valid image is picked without asking.
            const valid = this.validFiles;
            this.selectedFile = valid.length === 1 ? valid[0].fileName : '';
          }
          this.cdr.detectChanges();
        },
        error: err => {
          this.loading = false;
          this.files = [];
          this.selectedFile = '';
          this.listMessage = `H2H image listing failed: ${err?.message || err}`;
          this.firmwareMessage = '';
          this.firmwareWarning = false;
          this.cdr.detectChanges();
        },
      });
  }

  start(): void {
    const file = this.selected;
    if (!this.canStart || !file) {
      return;
    }
    this.setInProgress(true);
    this.progress = 0;
    this.phase = 'checking';
    this.phasePercent = 0;
    this.status = 'Starting the H2H flash...';
    this.result = 'NONE';
    this.log = [];
    this.elapsedS = 0;
    this.hostSerial = '';
    this.showSerialCard = false;
    this.flashSub?.unsubscribe();
    this.flashSub = this.mcuService
      .flashH2hFirmware(
        this.selectedPort,
        file.fileName,
        file.sha256,
        this.serialToWrite,
        this.readBack,
      )
      .subscribe({
        next: p => this.onProgress(p),
        error: err => {
          this.setInProgress(false);
          this.result = 'FAILED';
          this.status = `H2H flash error: ${err?.message || err}`;
          this.cdr.detectChanges();
        },
        complete: () => {
          this.setInProgress(false);
          this.cdr.detectChanges();
        },
      });
  }

  private onProgress(p: MauiH2hFlashProgress): void {
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
      this.hostSerial = p.hostSerial;
      this.showSerialCard = p.success && p.mcuSerialMismatch;
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
}
