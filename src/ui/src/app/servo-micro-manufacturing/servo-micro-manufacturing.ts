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
import {ServoMicroManufacturingService} from '../services/servo-micro-manufacturing.service';
import {TestPassFailButton} from '../test-pass-fail-button/test-pass-fail-button';
import {
  SubmitMicroProvisioningResultsRequest,
  StepResult,
} from '../../proto/servo-manufacturing.pb';

@Component({
  selector: 'app-servo-micro-manufacturing',
  standalone: true,
  templateUrl: './servo-micro-manufacturing.html',
  styleUrl: './servo-micro-manufacturing.scss',
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
export class ServoMicroManufacturingComponent implements AfterViewInit, OnInit {
  private readonly servoManufacturingService = inject(
    ServoMicroManufacturingService,
  );
  private readonly cdr = inject(ChangeDetectorRef);
  private readonly dialog = inject(MatDialog);

  mcuProgrammingResult = new StepResult();
  serialProgrammingResult = new StepResult();
  testingResult = new StepResult();

  @ViewChild('servoSerialNumberInput')
  servoSerialNumberInput!: ElementRef<HTMLInputElement>;

  @ViewChild('mcuStartButton', {read: ElementRef})
  mcuStartButton!: ElementRef<HTMLButtonElement>;

  @ViewChild('serialStartButton', {read: ElementRef})
  serialStartButton!: ElementRef<HTMLButtonElement>;

  @ViewChild('testStartButton', {read: ElementRef})
  testStartButton!: ElementRef<HTMLButtonElement>;

  @ViewChild('completeProvisioningButton', {read: ElementRef})
  completeProvisioningButton!: ElementRef<HTMLButtonElement>;

  @ViewChild('serialNumberStatus')
  serialNumberStatus!: TestPassFailButton;

  @ViewChild('mcuStatus')
  mcuStatus!: TestPassFailButton;

  @ViewChild('serialStatus')
  serialStatus!: TestPassFailButton;

  @ViewChild('testingStatus')
  testingStatus!: TestPassFailButton;

  servoSerialNumber = '';
  isSerialNumberValid = false;

  mcuProgrammingInProgress = false;
  mcuProgrammingSuccessful = false;
  serialNumberProgrammingInProgress = false;
  serialNumberProgrammingSuccessful = false;
  testingInProgress = false;
  testingSuccessful = false;

  private serialNumberSubject = new Subject<string>();

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
          this.mcuStartButton.nativeElement.focus();
        } else {
          this.serialNumberStatus.showFailed();
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
    this.mcuProgrammingSuccessful = false;
    this.serialNumberProgrammingSuccessful = false;
    this.testingSuccessful = false;
    this.serialNumberStatus.hide();
    if (this.servoSerialNumber) {
      this.serialNumberSubject.next(this.servoSerialNumber);
    }
  }

  startMcuProgramming() {
    this.mcuProgrammingInProgress = true;
    this.mcuProgrammingResult.retryCount++;
    this.mcuProgrammingSuccessful = false;
    this.serialNumberProgrammingSuccessful = false;
    this.testingSuccessful = false;
    this.mcuStatus.hide();

    this.servoManufacturingService.programMcu('').subscribe({
      next: response => {
        if (response.success) {
          this.mcuStatus.showPassed();
          this.mcuProgrammingSuccessful = true;
          this.mcuProgrammingResult.success = true;
          setTimeout(() => {
            this.serialStartButton.nativeElement.focus();
          }, 0);
        } else {
          this.mcuProgrammingResult.failureLogs.push(response.message);
          this.mcuStatus.showFailed();
          this.dialog.open(DialogOops, {
            data:
              response.message + (response.logs ? '\n\n' + response.logs : ''),
          });
        }
        this.mcuProgrammingInProgress = false;
        this.cdr.detectChanges();
      },
      error: err => {
        this.mcuProgrammingResult.failureLogs.push(
          err.message || 'Unknown error during MCU programming',
        );
        this.mcuStatus.showFailed();
        this.mcuProgrammingInProgress = false;
        this.dialog.open(DialogOops, {
          data: err.message || 'Unknown error during MCU programming',
        });
        this.cdr.detectChanges();
      },
    });
  }

  startSerialNumberProgramming() {
    this.serialNumberProgrammingInProgress = true;
    this.serialProgrammingResult.retryCount++;
    this.serialNumberProgrammingSuccessful = false;
    this.testingSuccessful = false;
    this.serialStatus.hide();

    this.servoManufacturingService
      .programSerial(this.servoSerialNumber)
      .subscribe({
        next: response => {
          if (response.success) {
            this.serialStatus.showPassed();
            this.serialNumberProgrammingSuccessful = true;
            this.serialProgrammingResult.success = true;
            setTimeout(() => {
              this.testStartButton.nativeElement.focus();
            }, 0);
          } else {
            this.serialProgrammingResult.failureLogs.push(response.message);
            this.serialStatus.showFailed();
            this.dialog.open(DialogOops, {
              data:
                response.message +
                (response.logs ? '\n\n' + response.logs : ''),
            });
          }
          this.serialNumberProgrammingInProgress = false;
          this.cdr.detectChanges();
        },
        error: err => {
          this.serialProgrammingResult.failureLogs.push(
            err.message || 'Unknown error during serial programming',
          );
          this.serialStatus.showFailed();
          this.serialNumberProgrammingInProgress = false;
          this.dialog.open(DialogOops, {
            data: err.message || 'Unknown error during serial programming',
          });
          this.cdr.detectChanges();
        },
      });
  }

  runTesting() {
    this.testingInProgress = true;
    this.testingResult.retryCount++;
    this.testingSuccessful = false;
    this.testingStatus.hide();

    this.servoManufacturingService.runTests(this.servoSerialNumber).subscribe({
      next: response => {
        if (response.allPassed) {
          this.testingStatus.showPassed();
          this.testingSuccessful = true;
          this.testingResult.success = true;
          setTimeout(() => {
            this.completeProvisioningButton.nativeElement.focus();
          }, 0);
        } else {
          this.testingResult.failureLogs.push(response.summary);
          this.testingStatus.showFailed();
          this.dialog.open(DialogOops, {
            data: response.summary,
          });
        }
        this.testingInProgress = false;
        this.cdr.detectChanges();
      },
      error: err => {
        this.testingResult.failureLogs.push(
          err.message || 'Unknown error during functional verification',
        );
        this.testingStatus.showFailed();
        this.testingInProgress = false;
        this.dialog.open(DialogOops, {
          data: err.message || 'Unknown error during functional verification',
        });
        this.cdr.detectChanges();
      },
    });
  }

  onReload() {
    const request = new SubmitMicroProvisioningResultsRequest({
      serialNumber: this.servoSerialNumber,
      mcuProgramming: this.mcuProgrammingResult,
      serialProgramming: this.serialProgrammingResult,
      testing: this.testingResult,
    });

    this.servoManufacturingService
      .submitProvisioningResults(request)
      .subscribe({
        next: () => {
          location.reload();
        },
        error: err => {
          console.error('Failed to submit results:', err);
          // Reload anyway to unblock the user
          location.reload();
        },
      });
  }
}
