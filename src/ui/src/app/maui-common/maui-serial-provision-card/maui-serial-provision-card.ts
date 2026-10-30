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

/** Result of the serial provisioning card. */
export type MauiSerialProvisionStatus =
  | 'NOT_RUN'
  | 'PASSED'
  | 'FAILED'
  | 'BYPASSED';

/**
 * Maui v1.2 serial number card.
 *
 * Copies the H2H serial (programmed pre-SMT) into the MCU with
 * `serial set` + `reboot` and checks that the MCU re-enumerates with it.
 * A board whose MCU serial already matches passes straight away.
 */
@Component({
  selector: 'app-maui-serial-provision-card',
  standalone: true,
  templateUrl: './maui-serial-provision-card.html',
  styleUrl: './maui-serial-provision-card.scss',
  imports: [
    CommonModule,
    DiagnosemeReworkCard,
    DiagnosemeReworkCardColumn,
    MatButtonModule,
    MatIconModule,
    TestPassFailButton,
  ],
})
export class MauiSerialProvisionCardComponent implements OnDestroy {
  @Input() cardId = 'func-card-serial';
  @Input() cardTitle = 'Serial Number Provisioning (H2H → MCU)';
  /**
   * Locks the card while other tests run: provisioning reboots the MCU and
   * must never interrupt a running test.
   */
  @Input() locked = false;
  /** Result of a run or bypass; |message| is the text shown in the card. */
  @Output() readonly statusChange = new EventEmitter<{
    status: MauiSerialProvisionStatus;
    serial: string;
    message: string;
  }>();
  /** Emits true when provisioning starts and false when it ends. */
  @Output() readonly runningChange = new EventEmitter<boolean>();

  private readonly mcuService = inject(MauiMcuService);
  private readonly dialogService = inject(MauiDialogService);
  private readonly cdr = inject(ChangeDetectorRef);

  @ViewChild('serialStatus') serialStatus?: TestPassFailButton;

  inProgress = false;
  status: MauiSerialProvisionStatus = 'NOT_RUN';
  message = '';
  h2hSerial = '';
  previousMcuSerial = '';
  mcuSerial = '';
  devicePort = '';
  reenumerationS = 0;
  alreadyProvisioned = false;
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
    this.message = 'Writing serial number to the MCU and waiting for reboot...';
    this.serialStatus?.showWorking();
    this.cdr.detectChanges();
    this.runSub = this.mcuService.provisionMcuSerial().subscribe({
      next: res => {
        this.setRunning(false);
        this.h2hSerial = res.h2hSerial;
        this.previousMcuSerial = res.previousMcuSerial;
        this.mcuSerial = res.mcuSerial;
        this.devicePort = res.devicePort;
        this.reenumerationS = res.reenumerationS;
        this.alreadyProvisioned = res.alreadyProvisioned;
        this.message = res.message;
        if (res.success) {
          this.setStatus('PASSED');
          this.serialStatus?.showPassed();
        } else {
          this.fail('Serial Number Provisioning Failed', res.message);
        }
        this.cdr.detectChanges();
      },
      error: err => {
        this.setRunning(false);
        this.message = err?.message || 'gRPC communication failure';
        this.fail('Serial Number Provisioning Error', this.message);
        this.cdr.detectChanges();
      },
    });
  }

  bypass(): void {
    this.message = 'Serial number provisioning bypassed by operator.';
    this.serialStatus?.showBypassed();
    this.setStatus('BYPASSED');
    this.cdr.detectChanges();
  }

  private fail(title: string, detail: string): void {
    this.setStatus('FAILED');
    this.serialStatus?.showFailed();
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

  private setStatus(status: MauiSerialProvisionStatus): void {
    this.status = status;
    this.statusChange.emit({
      status,
      serial: this.mcuSerial || this.h2hSerial,
      message: this.message,
    });
  }
}
