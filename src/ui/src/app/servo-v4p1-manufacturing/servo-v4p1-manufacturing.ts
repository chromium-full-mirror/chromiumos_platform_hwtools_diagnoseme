// Copyright 2026 The ChromiumOS Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

import {
  AfterViewInit,
  ChangeDetectorRef,
  Component,
  ElementRef,
  ViewChild,
  inject,
} from '@angular/core';
import {CommonModule} from '@angular/common';
import {FormsModule} from '@angular/forms';
import {MatButtonModule} from '@angular/material/button';
import {MatDialog} from '@angular/material/dialog';
import {MatFormFieldModule} from '@angular/material/form-field';
import {MatIconModule} from '@angular/material/icon';
import {MatInputModule} from '@angular/material/input';
import {MatToolbarModule} from '@angular/material/toolbar';
import {DiagnosemeReworkCard} from '../diagnoseme-rework-card/diagnoseme-rework-card';
import {DiagnosemeReworkCardColumn} from '../diagnoseme-rework-card-column/diagnoseme-rework-card-column';
import {DialogOops} from '../dolos-provision/dolos-provision';
import {HelpDialogButton} from '../help-dialog-button/help-dialog-button';
import {ServoManufacturingService} from '../services/servo-manufacturing.service';
import {TestPassFailButton} from '../test-pass-fail-button/test-pass-fail-button';

@Component({
  selector: 'app-servo-v4p1-manufacturing',
  standalone: true,
  templateUrl: './servo-v4p1-manufacturing.html',
  styleUrl: './servo-v4p1-manufacturing.scss',
  imports: [
    CommonModule,
    DiagnosemeReworkCard,
    DiagnosemeReworkCardColumn,
    FormsModule,
    MatButtonModule,
    MatFormFieldModule,
    MatIconModule,
    MatInputModule,
    HelpDialogButton,
    MatToolbarModule,
    TestPassFailButton,
  ],
})
export class ServoV4p1ManufacturingComponent implements AfterViewInit {
  private readonly servoManufacturingService = inject(
    ServoManufacturingService,
  );
  private readonly cdr = inject(ChangeDetectorRef);
  private readonly dialog = inject(MatDialog);

  @ViewChild('servoSerialNumberInput')
  servoSerialNumberInput!: ElementRef<HTMLInputElement>;

  @ViewChild('servoMacAddressInput')
  servoMacAddressInput!: ElementRef<HTMLInputElement>;

  @ViewChild('serialNumberStatus')
  serialNumberStatus!: TestPassFailButton;

  @ViewChild('macAddressStatus')
  macAddressStatus!: TestPassFailButton;

  @ViewChild('genesysHubStatus')
  genesysHubStatus!: TestPassFailButton;

  @ViewChild('mcuStatus')
  mcuStatus!: TestPassFailButton;

  servoSerialNumber = '';
  servoMacAddress = '';
  isSerialNumberValid = false;
  isMacAddressValid = false;
  hostSideProgrammingInProgress = false;

  ngAfterViewInit() {
    this.servoSerialNumberInput.nativeElement.focus();
  }

  onServoSerialNumberChange(event: Event) {
    const target = event.target as HTMLInputElement;
    this.servoSerialNumber = target.value;
    this.isSerialNumberValid = false;
    if (!this.servoSerialNumber) {
      this.serialNumberStatus.hide();
      return;
    }
    this.servoManufacturingService
      .validateServoSerial(this.servoSerialNumber)
      .subscribe(response => {
        this.isSerialNumberValid = response.isValid;
        if (this.isSerialNumberValid) {
          this.serialNumberStatus.showPassed();
          this.servoMacAddressInput.nativeElement.focus();
        } else {
          this.serialNumberStatus.showFailed();
        }
        this.cdr.detectChanges();
      });
  }

  onServoMacAddressChange(event: Event) {
    const target = event.target as HTMLInputElement;
    this.servoMacAddress = target.value;
    this.isMacAddressValid = false;
    if (!this.servoMacAddress) {
      this.macAddressStatus.hide();
      return;
    }
    this.servoManufacturingService
      .validateServoMacAddress(this.servoMacAddress)
      .subscribe(response => {
        this.isMacAddressValid = response.isValid;
        if (this.isMacAddressValid) {
          this.macAddressStatus.showPassed();
        } else {
          this.macAddressStatus.showFailed();
        }
        this.cdr.detectChanges();
      });
  }

  canStartHostSideProgramming(): boolean {
    return this.isSerialNumberValid && this.isMacAddressValid;
  }

  startHostSideProgramming() {
    this.hostSideProgrammingInProgress = true;
    this.genesysHubStatus.showWorking();
    this.mcuStatus.hide();

    this.servoManufacturingService.programGenesysHub().subscribe({
      next: response => {
        if (response.success) {
          this.genesysHubStatus.showPassed();
          this.mcuStatus.showWorking();
          this.servoManufacturingService.programMcu('').subscribe({
            next: mcuResponse => {
              if (mcuResponse.success) {
                this.mcuStatus.showPassed();
              } else {
                this.mcuStatus.showFailed();
                this.dialog.open(DialogOops, {
                  data: mcuResponse.message,
                });
              }
              this.hostSideProgrammingInProgress = false;
              this.cdr.detectChanges();
            },
            error: err => {
              this.mcuStatus.showFailed();
              this.hostSideProgrammingInProgress = false;
              this.dialog.open(DialogOops, {
                data: err.message || 'Unknown error during MCU programming',
              });
              this.cdr.detectChanges();
            },
          });
        } else {
          this.genesysHubStatus.showFailed();
          this.hostSideProgrammingInProgress = false;
          this.dialog.open(DialogOops, {
            data: response.message,
          });
          this.cdr.detectChanges();
        }
      },
      error: err => {
        this.genesysHubStatus.showFailed();
        this.hostSideProgrammingInProgress = false;
        this.dialog.open(DialogOops, {
          data: err.message || 'Unknown error during Genesys Hub programming',
        });
        this.cdr.detectChanges();
      },
    });
  }
  startDutSideProgramming() {
    // TODO(b/362261913): Implement DUT side programming
    console.log('Start DUT side programming');
  }
}
