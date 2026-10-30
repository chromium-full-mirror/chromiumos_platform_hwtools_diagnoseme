// Copyright 2026 The ChromiumOS Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

import {
  ChangeDetectorRef,
  Component,
  EventEmitter,
  Input,
  OnDestroy,
  Output,
  ViewChild,
  inject,
} from '@angular/core';
import {CommonModule} from '@angular/common';
import {MatButtonModule} from '@angular/material/button';
import {MatIconModule} from '@angular/material/icon';
import {Subscription} from 'rxjs';
import {DiagnosemeReworkCard} from '../../diagnoseme-rework-card/diagnoseme-rework-card';
import {DiagnosemeReworkCardColumn} from '../../diagnoseme-rework-card-column/diagnoseme-rework-card-column';
import {MauiDialogService} from '../maui-dialog.service';
import {MauiMcuService} from '../../services/maui-mcu.service';
import {TestPassFailButton} from '../../test-pass-fail-button/test-pass-fail-button';

/** Result of the H2H SPI flash access card. */
export type MauiH2hSpiCheckStatus =
  | 'NOT_RUN'
  | 'PASSED'
  | 'FAILED'
  | 'BYPASSED';

/**
 * Maui v1.2 H2H flash access card.
 *
 * The MCU reads the unit serial from the SPI flash of the H2H bridge
 * (`h2h_flash acquire/id/read/release`) and the backend compares it with
 * the serial the host sees before the check and after an explicit reset of
 * the bridge. Only approved MCU App builds may access the flash; on other
 * builds the card fails with "update the MCU App" without touching the flash.
 */
@Component({
  selector: 'app-maui-h2h-spi-check-card',
  standalone: true,
  templateUrl: './maui-h2h-spi-check-card.html',
  styleUrl: './maui-h2h-spi-check-card.scss',
  imports: [
    CommonModule,
    DiagnosemeReworkCard,
    DiagnosemeReworkCardColumn,
    MatButtonModule,
    MatIconModule,
    TestPassFailButton,
  ],
})
export class MauiH2hSpiCheckCardComponent implements OnDestroy {
  @Input() cardId = 'func-card-h2h-spi';
  @Input() cardTitle = 'H2H Flash Access Check (SPI)';
  /**
   * Locks the card while other tests run: the check resets the H2H bridge,
   * which drops off USB for about half a second.
   */
  @Input() locked = false;
  /** Unit serial (log directory); empty: the backend uses the H2H serial. */
  @Input() serialNumber = '';
  /** MCU console; empty: the backend uses the first MCU App console. */
  @Input() devicePort = '';
  /** Result of a run or bypass; |message| is the text shown in the card. */
  @Output() readonly statusChange = new EventEmitter<{
    status: MauiH2hSpiCheckStatus;
    serial: string;
    message: string;
  }>();
  /** Emits true when the check starts and false when it ends. */
  @Output() readonly runningChange = new EventEmitter<boolean>();

  private readonly mcuService = inject(MauiMcuService);
  private readonly dialogService = inject(MauiDialogService);
  private readonly cdr = inject(ChangeDetectorRef);

  @ViewChild('h2hSpiStatus') h2hSpiStatus?: TestPassFailButton;

  inProgress = false;
  status: MauiH2hSpiCheckStatus = 'NOT_RUN';
  message = '';
  firmwareSupported = true;
  hostSerialBefore = '';
  spiSerial = '';
  hostSerialAfter = '';
  jedecId = '';
  flashSize = '';
  private runSub?: Subscription;

  get done(): boolean {
    return this.status === 'PASSED' || this.status === 'BYPASSED';
  }

  ngOnDestroy(): void {
    this.runSub?.unsubscribe();
  }

  run(): void {
    if (this.inProgress || this.locked) {
      return;
    }
    this.runSub?.unsubscribe();
    this.setRunning(true);
    this.message = 'Reading the serial number from the H2H SPI flash...';
    this.h2hSpiStatus?.showWorking();
    this.cdr.detectChanges();
    this.runSub = this.mcuService
      .checkH2hSpiSerial(this.serialNumber, this.devicePort)
      .subscribe({
        next: res => {
          this.setRunning(false);
          this.firmwareSupported = res.firmwareSupported;
          this.hostSerialBefore = res.hostSerialBefore;
          this.spiSerial = res.spiSerial;
          this.hostSerialAfter = res.hostSerialAfter;
          this.jedecId = res.jedecId;
          this.flashSize = res.flashSize;
          this.message = res.message;
          if (res.success) {
            this.setStatus('PASSED');
            this.h2hSpiStatus?.showPassed();
          } else {
            this.fail(
              res.firmwareSupported
                ? 'H2H Flash Access Check Failed'
                : 'MCU App Update Required',
              res.message,
            );
          }
          this.cdr.detectChanges();
        },
        error: err => {
          this.setRunning(false);
          this.message = err?.message || 'gRPC communication failure';
          this.fail('H2H Flash Access Check Error', this.message);
          this.cdr.detectChanges();
        },
      });
  }

  bypass(): void {
    this.message = 'H2H flash access check bypassed by operator.';
    this.h2hSpiStatus?.showBypassed();
    this.setStatus('BYPASSED');
    this.cdr.detectChanges();
  }

  private fail(title: string, detail: string): void {
    this.setStatus('FAILED');
    this.h2hSpiStatus?.showFailed();
    this.dialogService.openFailure(
      title,
      detail,
      () => this.run(),
      () => this.bypass(),
    );
  }

  private setRunning(running: boolean): void {
    this.inProgress = running;
    this.runningChange.emit(running);
  }

  private setStatus(status: MauiH2hSpiCheckStatus): void {
    this.status = status;
    this.statusChange.emit({
      status,
      serial: this.spiSerial,
      message: this.message,
    });
  }
}
