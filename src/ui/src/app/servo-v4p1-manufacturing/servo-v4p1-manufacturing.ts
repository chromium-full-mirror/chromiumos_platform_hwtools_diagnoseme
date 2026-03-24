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
import {
  SubmitProvisioningResultsRequest,
  StepResult,
} from '../../proto/servo-manufacturing.pb';

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

  hostProgrammingResult = new StepResult();
  serialProgrammingResult = new StepResult();
  dutProgrammingResult = new StepResult();
  functionalTestingResult = new StepResult();
  integrationTestingResult = new StepResult();

  @ViewChild('servoSerialNumberInput')
  servoSerialNumberInput!: ElementRef<HTMLInputElement>;

  @ViewChild('servoMacAddressInput')
  servoMacAddressInput!: ElementRef<HTMLInputElement>;

  @ViewChild('hostStartButton', {read: ElementRef})
  hostStartButton!: ElementRef<HTMLButtonElement>;

  @ViewChild('serialStartButton', {read: ElementRef})
  serialStartButton!: ElementRef<HTMLButtonElement>;

  @ViewChild('dutStartButton', {read: ElementRef})
  dutStartButton!: ElementRef<HTMLButtonElement>;

  @ViewChild('functionalTestStartButton', {read: ElementRef})
  functionalTestStartButton!: ElementRef<HTMLButtonElement>;

  @ViewChild('integrationTestStartButton', {read: ElementRef})
  integrationTestStartButton!: ElementRef<HTMLButtonElement>;

  @ViewChild('completeProvisioningButton', {read: ElementRef})
  completeProvisioningButton!: ElementRef<HTMLButtonElement>;

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

  @ViewChild('atmegaStatus')
  atmegaStatus!: TestPassFailButton;

  @ViewChild('consoleTestingStatus')
  consoleTestingStatus!: TestPassFailButton;

  @ViewChild('functionalTestingStatus')
  functionalTestingStatus!: TestPassFailButton;

  @ViewChild('integrationTestingStatus')
  integrationTestingStatus!: TestPassFailButton;

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
  consoleTestingInProgress = false;
  consoleTestingSuccessful = false;
  functionalTestingInProgress = false;
  functionalTestingSuccessful = false;
  integrationTestingInProgress = false;
  integrationTestingSuccessful = false;

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
    this.hostProgrammingResult.retryCount++;
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
                this.hostProgrammingResult.success = true;
                setTimeout(() => {
                  this.serialStartButton.nativeElement.focus();
                }, 0);
              } else {
                this.hostProgrammingResult.failureLogs.push(
                  mcuResponse.message,
                );
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
              this.hostProgrammingResult.failureLogs.push(
                err.message || 'Unknown error during MCU programming',
              );
              this.mcuStatus.showFailed();
              this.hostSideProgrammingInProgress = false;
              this.dialog.open(DialogOops, {
                data: err.message || 'Unknown error during MCU programming',
              });
              this.cdr.detectChanges();
            },
          });
        } else {
          this.hostProgrammingResult.failureLogs.push(response.message);
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
        this.hostProgrammingResult.failureLogs.push(
          err.message || 'Unknown error during Genesys Hub programming',
        );
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
    this.serialProgrammingResult.retryCount++;
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
            this.serialProgrammingResult.success = true;
            setTimeout(() => {
              this.dutStartButton.nativeElement.focus();
            }, 0);
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
    this.dutProgrammingResult.retryCount++;
    this.dutSideProgrammingSuccessful = false;
    this.ethStatus.hide();
    this.atmegaStatus.hide();

    this.servoManufacturingService
      .programEthernet(this.servoMacAddress, this.servoSerialNumber)
      .subscribe({
        next: response => {
          if (response.success) {
            this.ethStatus.showPassed();
            this.servoManufacturingService
              .programAtmega(this.servoSerialNumber)
              .subscribe({
                next: atmegaResponse => {
                  if (atmegaResponse.success) {
                    this.atmegaStatus.showPassed();
                    this.dutSideProgrammingSuccessful = true;
                    this.dutProgrammingResult.success = true;
                    this.runFunctionalTestingMerged();
                  } else {
                    this.dutProgrammingResult.failureLogs.push(
                      atmegaResponse.message,
                    );
                    this.atmegaStatus.showFailed();
                    this.dialog.open(DialogOops, {
                      data:
                        atmegaResponse.message +
                        (atmegaResponse.logs
                          ? '\n\n' + atmegaResponse.logs
                          : ''),
                    });
                  }
                  this.dutSideProgrammingInProgress = false;
                  this.cdr.detectChanges();
                },
                error: err => {
                  this.atmegaStatus.showFailed();
                  this.dutSideProgrammingInProgress = false;
                  this.dialog.open(DialogOops, {
                    data:
                      err.message || 'Unknown error during Atmega programming',
                  });
                  this.cdr.detectChanges();
                },
              });
          } else {
            this.ethStatus.showFailed();
            this.dialog.open(DialogOops, {
              data:
                response.message +
                (response.logs ? '\n\n' + response.logs : ''),
            });
            this.dutSideProgrammingInProgress = false;
            this.cdr.detectChanges();
          }
        },
        error: err => {
          this.dutProgrammingResult.failureLogs.push(
            err.message || 'Unknown error during Ethernet programming',
          );
          this.ethStatus.showFailed();
          this.dutSideProgrammingInProgress = false;
          this.dialog.open(DialogOops, {
            data: err.message || 'Unknown error during Ethernet programming',
          });
          this.cdr.detectChanges();
        },
      });
  }

  runFunctionalTestingMerged() {
    this.consoleTestingInProgress = true;
    this.functionalTestingResult.retryCount++;
    this.consoleTestingSuccessful = false;
    this.functionalTestingSuccessful = false;
    this.consoleTestingStatus.hide();
    this.functionalTestingStatus.hide();

    this.servoManufacturingService
      .runConsoleTests(this.servoSerialNumber, this.servoMacAddress)
      .subscribe({
        next: response => {
          if (response.allPassed) {
            this.consoleTestingStatus.showPassed();
            this.consoleTestingSuccessful = true;
            this.consoleTestingInProgress = false;

            // Start the second part: Functional Tests (servod recovery)
            this.functionalTestingInProgress = true;
            this.servoManufacturingService
              .runFunctionalTests(this.servoSerialNumber, this.servoMacAddress)
              .subscribe({
                next: functionalResponse => {
                  if (functionalResponse.allPassed) {
                    this.functionalTestingStatus.showPassed();
                    this.functionalTestingSuccessful = true;
                    this.functionalTestingResult.success = true;
                    setTimeout(() => {
                      this.integrationTestStartButton.nativeElement.focus();
                    }, 0);
                  } else {
                    this.functionalTestingResult.failureLogs.push(
                      functionalResponse.summary,
                    );
                    this.functionalTestingStatus.showFailed();
                    this.dialog.open(DialogOops, {
                      data: functionalResponse.summary,
                    });
                  }
                  this.functionalTestingInProgress = false;
                  this.cdr.detectChanges();
                },
                error: err => {
                  this.functionalTestingStatus.showFailed();
                  this.functionalTestingInProgress = false;
                  this.dialog.open(DialogOops, {
                    data:
                      err.message ||
                      'Unknown error during functional verification',
                  });
                  this.cdr.detectChanges();
                },
              });
          } else {
            this.functionalTestingResult.failureLogs.push(response.summary);
            this.consoleTestingStatus.showFailed();
            this.consoleTestingInProgress = false;
            this.dialog.open(DialogOops, {
              data: response.summary,
            });
          }
          this.cdr.detectChanges();
        },
        error: err => {
          this.consoleTestingStatus.showFailed();
          this.consoleTestingInProgress = false;
          this.dialog.open(DialogOops, {
            data:
              err.message ||
              'Unknown error during direct hardware verification',
          });
          this.cdr.detectChanges();
        },
      });
  }

  runIntegrationTests() {
    this.integrationTestingInProgress = true;
    this.integrationTestingResult.retryCount++;
    this.integrationTestingSuccessful = false;
    this.integrationTestingStatus.hide();

    this.servoManufacturingService
      .runIntegrationTests(this.servoSerialNumber, this.servoMacAddress)
      .subscribe({
        next: response => {
          if (response.allPassed) {
            this.integrationTestingStatus.showPassed();
            this.integrationTestingSuccessful = true;
            this.integrationTestingResult.success = true;
            setTimeout(() => {
              this.completeProvisioningButton.nativeElement.focus();
            }, 0);
          } else {
            this.integrationTestingResult.failureLogs.push(response.summary);
            this.integrationTestingStatus.showFailed();
            this.dialog.open(DialogOops, {
              data: response.summary,
            });
          }
          this.integrationTestingInProgress = false;
          this.cdr.detectChanges();
        },
        error: err => {
          this.integrationTestingResult.failureLogs.push(
            err.message || 'Unknown error during integration verification',
          );
          this.integrationTestingStatus.showFailed();
          this.integrationTestingInProgress = false;
          this.dialog.open(DialogOops, {
            data:
              err.message || 'Unknown error during integration verification',
          });
          this.cdr.detectChanges();
        },
      });
  }

  onReload() {
    const request = new SubmitProvisioningResultsRequest({
      serialNumber: this.servoSerialNumber,
      macAddress: this.servoMacAddress,
      hostProgramming: this.hostProgrammingResult,
      serialProgramming: this.serialProgrammingResult,
      dutProgramming: this.dutProgrammingResult,
      functionalTesting: this.functionalTestingResult,
      integrationTesting: this.integrationTestingResult,
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
