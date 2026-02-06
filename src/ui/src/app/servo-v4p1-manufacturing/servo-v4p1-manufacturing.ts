// Copyright 2026 The ChromiumOS Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

import {
  AfterViewInit,
  ChangeDetectorRef,
  Component,
  ElementRef,
  OnInit,
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
import {Subject} from 'rxjs';
import {debounceTime, switchMap} from 'rxjs/operators';
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
export class ServoV4p1ManufacturingComponent implements AfterViewInit, OnInit {
  private readonly servoManufacturingService = inject(
    ServoManufacturingService,
  );
  private readonly cdr = inject(ChangeDetectorRef);
  private readonly dialog = inject(MatDialog);

  @ViewChild('servoSerialNumberInput')
  servoSerialNumberInput!: ElementRef<HTMLInputElement>;

  @ViewChild('servoMacAddressInput')
  servoMacAddressInput!: ElementRef<HTMLInputElement>;

  @ViewChild('hostStartButton', {read: ElementRef})
  hostStartButton!: ElementRef<HTMLButtonElement>;

  @ViewChild('serialNumberStatus')
  serialNumberStatus!: TestPassFailButton;

  @ViewChild('macAddressStatus')
  macAddressStatus!: TestPassFailButton;

  @ViewChild('genesysHubStatus')
  genesysHubStatus!: TestPassFailButton;

  @ViewChild('mcuStatus')
  mcuStatus!: TestPassFailButton;

  @ViewChild('serialStatus')
  serialStatus!: TestPassFailButton;

  @ViewChild('ethStatus')
  ethStatus!: TestPassFailButton;

  servoSerialNumber = '';
  servoMacAddress = '';
  isSerialNumberValid = false;
  isMacAddressValid = false;
  hostSideProgrammingInProgress = false;
  hostSideProgrammingSuccessful = false;
  serialNumberProgrammingInProgress = false;
  serialNumberProgrammingSuccessful = false;
  dutSideProgrammingInProgress = false;
  dutSideProgrammingSuccessful = false;

  private serialNumberSubject = new Subject<string>();
  private macAddressSubject = new Subject<string>();

  ngOnInit() {
    this.serialNumberSubject
      .pipe(
        debounceTime(500),
        switchMap(serial =>
          this.servoManufacturingService.validateServoSerial(serial),
        ),
      )
      .subscribe(response => {
        this.isSerialNumberValid = response.isValid;
        if (this.isSerialNumberValid) {
          this.serialNumberStatus.showPassed();
          this.servoMacAddressInput.nativeElement.focus();
          this.cdr.detectChanges();
          if (this.isMacAddressValid) {
            this.hostStartButton.nativeElement.focus();
          }
        } else {
          this.serialNumberStatus.showFailed();
        }
        this.cdr.detectChanges();
      });

    this.macAddressSubject
      .pipe(
        debounceTime(500),
        switchMap(mac =>
          this.servoManufacturingService.validateServoMacAddress(mac),
        ),
      )
      .subscribe(response => {
        this.isMacAddressValid = response.isValid;
        if (this.isMacAddressValid) {
          this.macAddressStatus.showPassed();
          this.cdr.detectChanges();
          if (this.isSerialNumberValid) {
            this.hostStartButton.nativeElement.focus();
          }
        } else {
          this.macAddressStatus.showFailed();
        }
        this.cdr.detectChanges();
      });
  }

  ngAfterViewInit() {
    this.servoSerialNumberInput.nativeElement.focus();
  }

  onServoSerialNumberChange(event: Event) {
    const target = event.target as HTMLInputElement;
    this.servoSerialNumber = target.value.trim().toUpperCase();
    target.value = this.servoSerialNumber;
    this.isSerialNumberValid = false;
    this.hostSideProgrammingSuccessful = false;
    this.serialNumberProgrammingSuccessful = false;
    this.dutSideProgrammingSuccessful = false;
    this.serialNumberStatus.hide();
    if (this.servoSerialNumber) {
      this.serialNumberSubject.next(this.servoSerialNumber);
    }
  }

  onServoMacAddressChange(event: Event) {
    const target = event.target as HTMLInputElement;
    this.servoMacAddress = target.value.trim().toUpperCase();
    target.value = this.servoMacAddress;
    this.isMacAddressValid = false;
    this.hostSideProgrammingSuccessful = false;
    this.serialNumberProgrammingSuccessful = false;
    this.dutSideProgrammingSuccessful = false;
    this.macAddressStatus.hide();
    if (this.servoMacAddress) {
      this.macAddressSubject.next(this.servoMacAddress);
    }
  }

  canStartHostSideProgramming(): boolean {
    return this.isSerialNumberValid && this.isMacAddressValid;
  }

  startHostSideProgramming() {
    this.hostSideProgrammingInProgress = true;
    this.hostSideProgrammingSuccessful = false;
    this.serialNumberProgrammingSuccessful = false;
    this.genesysHubStatus.hide();
    this.mcuStatus.hide();

    this.servoManufacturingService.programGenesysHub().subscribe({
      next: response => {
        if (response.success) {
          this.genesysHubStatus.showPassed();
          this.servoManufacturingService.programMcu('').subscribe({
            next: mcuResponse => {
              if (mcuResponse.success) {
                this.mcuStatus.showPassed();
                this.hostSideProgrammingSuccessful = true;
              } else {
                this.mcuStatus.showFailed();
                this.dialog.open(DialogOops, {
                  data:
                    mcuResponse.message +
                    (mcuResponse.logs ? '\n\n' + mcuResponse.logs : ''),
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
            data:
              response.message + (response.logs ? '\n\n' + response.logs : ''),
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

  startSerialNumberProgramming() {
    this.serialNumberProgrammingInProgress = true;
    this.serialNumberProgrammingSuccessful = false;
    this.dutSideProgrammingSuccessful = false;
    this.serialStatus.hide();

    this.servoManufacturingService
      .programSerial(this.servoSerialNumber)
      .subscribe({
        next: serialResponse => {
          if (serialResponse.success) {
            this.serialStatus.showPassed();
            this.serialNumberProgrammingSuccessful = true;
          } else {
            this.serialStatus.showFailed();
            this.dialog.open(DialogOops, {
              data:
                serialResponse.message +
                (serialResponse.logs ? '\n\n' + serialResponse.logs : ''),
            });
          }
          this.serialNumberProgrammingInProgress = false;
          this.cdr.detectChanges();
        },
        error: err => {
          this.serialStatus.showFailed();
          this.serialNumberProgrammingInProgress = false;
          this.dialog.open(DialogOops, {
            data: err.message || 'Unknown error during serial programming',
          });
          this.cdr.detectChanges();
        },
      });
  }

  startDutSideProgramming() {
    this.dutSideProgrammingInProgress = true;
    this.dutSideProgrammingSuccessful = false;
    this.ethStatus.hide();

    this.servoManufacturingService
      .programEthernet(this.servoMacAddress, this.servoSerialNumber)
      .subscribe({
        next: response => {
          if (response.success) {
            this.ethStatus.showPassed();
            this.dutSideProgrammingSuccessful = true;
          } else {
            this.ethStatus.showFailed();
            this.dialog.open(DialogOops, {
              data:
                response.message +
                (response.logs ? '\n\n' + response.logs : ''),
            });
          }
          this.dutSideProgrammingInProgress = false;
          this.cdr.detectChanges();
        },
        error: err => {
          this.ethStatus.showFailed();
          this.dutSideProgrammingInProgress = false;
          this.dialog.open(DialogOops, {
            data: err.message || 'Unknown error during Ethernet programming',
          });
          this.cdr.detectChanges();
        },
      });
  }
}
