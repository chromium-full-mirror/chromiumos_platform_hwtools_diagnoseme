// Copyright 2025 The ChromiumOS Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

import {CommonModule} from '@angular/common';
import {Component, inject} from '@angular/core';
import {FormsModule} from '@angular/forms';
import {MatButtonModule} from '@angular/material/button';
import {MatFormFieldModule} from '@angular/material/form-field';
import {MatIconModule} from '@angular/material/icon';
import {MatInputModule} from '@angular/material/input';
import {MatToolbarModule} from '@angular/material/toolbar';
import {DiagnosemeReworkCard} from '../diagnoseme-rework-card/diagnoseme-rework-card';
import {DiagnosemeReworkCardColumn} from '../diagnoseme-rework-card-column/diagnoseme-rework-card-column';
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
export class ServoV4p1ManufacturingComponent {
  private readonly servoManufacturingService = inject(
    ServoManufacturingService,
  );
  servoSerialNumber = '';
  servoMacAddress = '';
  isSerialNumberValid = false;
  isMacAddressValid = false;
  servoSerialNumberTouched = false;
  servoMacAddressTouched = false;

  onServoSerialNumberChange(event: Event) {
    const target = event.target as HTMLInputElement;
    this.servoSerialNumber = target.value;
    this.isSerialNumberValid = false; // Reset validity on change
    this.servoSerialNumberTouched = true;
    console.log('Servo serial number changed:', this.servoSerialNumber);
    if (!this.servoSerialNumber) {
      this.servoSerialNumberTouched = false;
      return;
    }
    this.servoManufacturingService
      .validateServoSerial(this.servoSerialNumber)
      .subscribe(response => {
        console.log('Validate servo serial response:', response);
        this.isSerialNumberValid = response.isValid;
      });
    console.log(this.isSerialNumberValid);
  }

  onServoMacAddressChange(event: Event) {
    const target = event.target as HTMLInputElement;
    this.servoMacAddress = target.value;
    this.isMacAddressValid = false; // Reset validity on change
    this.servoMacAddressTouched = true;
    console.log('Servo MAC address changed:', this.servoMacAddress);
    if (!this.servoMacAddress) {
      this.servoMacAddressTouched = false;
      return;
    }
    this.servoManufacturingService
      .validateServoMacAddress(this.servoMacAddress)
      .subscribe(response => {
        console.log('Validate servo MAC address response:', response);
        this.isMacAddressValid = response.isValid;
      });
  }

  getSerialNumberIcon(): string {
    if (!this.servoSerialNumberTouched) {
      return '';
    }
    return this.isSerialNumberValid ? 'check_circle' : 'error';
  }

  getSerialNumberIconColor(): string {
    if (!this.servoSerialNumberTouched) {
      return '';
    }
    return this.isSerialNumberValid ? 'green' : 'red';
  }

  getMacAddressIcon(): string {
    if (!this.servoMacAddressTouched) {
      return '';
    }
    return this.isMacAddressValid ? 'check_circle' : 'error';
  }

  getMacAddressIconColor(): string {
    if (!this.servoMacAddressTouched) {
      return '';
    }
    return this.isMacAddressValid ? 'green' : 'red';
  }

  canStartHostSideProgramming(): boolean {
    return this.isSerialNumberValid && this.isMacAddressValid;
  }

  startHostSideProgramming() {
    // TODO(b/362261913): Implement host side programming
    console.log('Start host side programming');
  }

  startDutSideProgramming() {
    // TODO(b/362261913): Implement DUT side programming
    console.log('Start DUT side programming');
  }
}
