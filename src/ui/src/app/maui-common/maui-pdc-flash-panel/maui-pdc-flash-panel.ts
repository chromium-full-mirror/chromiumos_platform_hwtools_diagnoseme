// Copyright 2026 The ChromiumOS Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

import {
  ChangeDetectorRef,
  Component,
  Input,
  OnDestroy,
  OnInit,
  inject,
} from '@angular/core';
import {CommonModule} from '@angular/common';
import {MatButtonModule} from '@angular/material/button';
import {MatIconModule} from '@angular/material/icon';
import {MatProgressBarModule} from '@angular/material/progress-bar';
import {Subscription} from 'rxjs';
import {MauiPdcService} from '../../services/maui-pdc.service';

/** State of one PDC flashing slot. */
export interface MauiPdcSlot {
  slotIndex: number;
  serialNumber: string;
  devicePort: string;
  product: string;
  state: 'IDLE' | 'FLASHING' | 'PASSED' | 'FAILED';
  progressPercent: number;
  statusMessage: string;
}

/**
 * Multi-slot PDC (TPS66994) flashing panel.
 *
 * Talks to MauiPdcFlashing; the edition backend decides how boards are found
 * (v1.1: FTDI consoles, v1.2: CDC-ACM consoles).
 */
@Component({
  selector: 'app-maui-pdc-flash-panel',
  standalone: true,
  templateUrl: './maui-pdc-flash-panel.html',
  styleUrl: './maui-pdc-flash-panel.scss',
  imports: [CommonModule, MatButtonModule, MatIconModule, MatProgressBarModule],
})
export class MauiPdcFlashPanelComponent implements OnInit, OnDestroy {
  @Input() maxSlots = 5;
  // PDC firmware channel; '' = the server's default PDC firmware.
  @Input() fwChannel = '';

  private readonly pdcService = inject(MauiPdcService);
  private readonly cdr = inject(ChangeDetectorRef);
  private readonly subs = new Subscription();

  slots: MauiPdcSlot[] = [];
  discovering = false;
  discoveryMessage = '';

  get anyFlashing(): boolean {
    return this.slots.some(s => s.state === 'FLASHING');
  }

  ngOnInit(): void {
    this.discover();
  }

  ngOnDestroy(): void {
    this.subs.unsubscribe();
  }

  discover(): void {
    if (this.anyFlashing) {
      return;
    }
    this.discovering = true;
    const sub = this.pdcService.discoverConnectedPdcDevices().subscribe({
      next: res => {
        this.discovering = false;
        this.discoveryMessage = res.message;
        this.slots = (res.devices || []).slice(0, this.maxSlots).map(d => ({
          slotIndex: d.slotIndex,
          serialNumber: d.serialNumber,
          devicePort: d.devicePort,
          product: d.productDescription,
          state: 'IDLE',
          progressPercent: 0,
          statusMessage: 'Ready',
        }));
        this.cdr.detectChanges();
      },
      error: err => {
        this.discovering = false;
        this.discoveryMessage = `Discovery failed: ${err?.message || err}`;
        this.cdr.detectChanges();
      },
    });
    this.subs.add(sub);
  }

  flashAll(): void {
    for (const slot of this.slots) {
      if (slot.state !== 'FLASHING') {
        this.flash(slot);
      }
    }
  }

  flash(slot: MauiPdcSlot): void {
    slot.state = 'FLASHING';
    slot.progressPercent = 0;
    slot.statusMessage = 'Starting PDC flashing...';
    this.cdr.detectChanges();
    this.subs.add(
      this.pdcService
        .programSinglePdc(
          slot.slotIndex,
          slot.serialNumber || `MAUI-SLOT${slot.slotIndex}`,
          slot.devicePort,
          this.fwChannel,
        )
        .subscribe({
          next: progress => {
            slot.progressPercent = progress.progressPercent;
            slot.statusMessage =
              progress.statusMessage ||
              `Flashing PDC... ${progress.progressPercent}%`;
            if (progress.isComplete) {
              if (progress.errorMessage) {
                slot.state = 'FAILED';
                slot.statusMessage = `PDC Flash Failed: ${progress.errorMessage}`;
              } else {
                slot.state = 'PASSED';
                slot.progressPercent = 100;
              }
            }
            this.cdr.detectChanges();
          },
          error: err => {
            slot.state = 'FAILED';
            slot.statusMessage = `PDC Flash Error: ${err?.message || err}`;
            this.cdr.detectChanges();
          },
        }),
    );
  }
}
