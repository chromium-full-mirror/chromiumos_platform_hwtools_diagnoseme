// Copyright 2025 The ChromiumOS Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

import { Component, ElementRef, Inject, QueryList, ViewChildren } from '@angular/core';
import { TestStatus, BoardModel } from '../../proto/diagnoseme-dolos.pb';
import { DolosService } from '../services/dolos.service';
import { FormsModule } from '@angular/forms'

import { MatIconModule } from '@angular/material/icon';
import { MatButtonModule } from '@angular/material/button';
import { MatCardModule } from '@angular/material/card';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatSelectModule } from '@angular/material/select';
import { MatToolbarModule } from '@angular/material/toolbar';
import { CommonModule } from '@angular/common';
import {
  MAT_DIALOG_DATA,
  MatDialog,
  MatDialogActions,
  MatDialogContent,
  MatDialogRef,
  MatDialogTitle,
} from '@angular/material/dialog';
import { MatInputModule } from '@angular/material/input';

import { CommandExecutionResults } from '../../proto/diagnoseme-dolos.pb';
import { ServodService } from '../services/servod-service';
import { MatCheckboxModule } from '@angular/material/checkbox';
import { DolosReworkCard } from '../dolos-rework-card/dolos-rework-card';
import { DolosReworkCardRow } from '../dolos-rework-card-row/dolos-rework-card-row';
import { DolosReworkCardColumn } from '../dolos-rework-card-column/dolos-rework-card-column';
import { HelpDialogButton } from '../help-dialog-button/help-dialog-button';


interface Model {
  name: string;
  config: CableConfig;
}

interface CableConfig {
  name: string;
  gpn: string;
}

export enum TestResult {
  NoResult,
  Pass,
  Fail,
}

@Component({
  selector: 'app-dolos-provision',
  templateUrl: './dolos-provision.component.html',
  styleUrl: './dolos-provision.component.scss',
  imports: [
    MatIconModule,
    MatCardModule,
    MatFormFieldModule,
    MatSelectModule,
    MatToolbarModule,
    FormsModule,
    MatButtonModule,
    CommonModule,
    MatInputModule,
    MatCheckboxModule,
    DolosReworkCard,
    DolosReworkCardRow,
    DolosReworkCardColumn,
    HelpDialogButton,
  ]
})

export class DolosProvisionComponent {

  @ViewChildren("gpn") gpnScanInput?: QueryList<ElementRef>;
  @ViewChildren("dolosserial") dolosSerialInput?: QueryList<ElementRef>;

  public TestResultEnum = TestResult;

  selectedModel: Model | null = null;

  hostTestProgress: boolean = false;
  firmwareProgress: boolean = false;
  cableProgramProgress: boolean = false;
  flashSerialProgress: boolean = false;
  disableAll: boolean = false;
  hwidProgress: boolean = false;
  bslMode: boolean = false;

  model: string = "";
  board: string = "";

  hostTestPass: TestResult = TestResult.NoResult;
  dolosSerialPass: TestResult = TestResult.NoResult;

  testVoltage: string = "";
  testPowerstate: string = "";

  dolosSerialScan: string = '';
  gpnScan: string = '';

  dutErrorMsg: string = '';
  hostErrorMsg: string = '';

  configs: CableConfig[] = [
    { name: 'config0', gpn: '' },
    { name: 'config1', gpn: '' },
    { name: 'config2', gpn: '600-02523-00' },
    { name: 'config3', gpn: '600-02524-00' },
    { name: 'config4', gpn: '' },
    { name: 'config5', gpn: '' },
    { name: 'config6', gpn: '' },
    { name: 'config7', gpn: '600-02522-00' }
  ];

  models: Model[] = [];
  boardModels: Map<string, string> = new Map();
  hwid: string = '';

  firmwareVersions: string[] = [];
  selectedFirmwareVersion: string = "";

  internalDolosSerialNumber: string = '';
  gpnPass: TestResult = TestResult.NoResult;

  constructor(public dialog: MatDialog, private dolosService: DolosService, private servodService: ServodService) {
    this.dolosService.getModels(this.updateModels)
    this.models.sort((a, b) =>
      a.name > b.name ? 1 : b.name > a.name ? -1 : 0
    );
    this.dolosService.getFirmwareVersions(this.updateFirmwareVersions)
    this.dolosService.getBoardModelMapping(this.updateBoardModelMapping)
  }

  updateBoardModelMapping = (mapping: BoardModel[]) => {
    this.boardModels.clear()
    mapping.forEach((boardModel: BoardModel) => {
      this.boardModels.set(boardModel.model, boardModel.board);
    })
  }

  updateModels = (models: string[]) => {
    this.models = []
    models.forEach((model: string) => {
      this.models.push({ name: model, config: this.configs[2] })
    })
  }

  updateFirmwareVersions = (firmwareVersions: string[], defaultFirmwareVersion: string) => {
    this.firmwareVersions = firmwareVersions;
    this.selectedFirmwareVersion = defaultFirmwareVersion;
  }

  onProbeHardware() {
    this.hwidProgress = true;
    this.servodService.startServod(this.board, this.model, this.servodStarted);
  }

  onChangeModel(newValue: Model) {
    this.selectedModel = newValue;
    this.gpnPass = TestResult.NoResult;
    this.gpnScan = '';
    setTimeout(() => {
      this.gpnScanInput?.get(0)?.nativeElement.focus()
    }, 10);
    this.model = this.selectedModel?.name;
    if (this.model == undefined) {
      this.model = "unknown";
    }
    let board = this.boardModels.get(this.model);
    if (board == undefined) {
      this.board = "unknown";
    } else {
      this.board = board;
    }

  }

  servodStarted = (started: boolean, console_output: string) => {
    console.log(started);
    if (started == true) {
      this.servodService.getHWID(this.updateHWID);
    } else {
      this.hwidProgress = false
      console.log(console_output)
      const dialogRef = this.dialog.open(DialogOops, {
        data: console_output
      });
    }
  }

  updateHWID = (hwid: string, output: string) => {
    console.log(output)
    if (hwid != "") {
      this.hwid = hwid
    } else {
      const dialogRef = this.dialog.open(DialogOops, {
        data: output
      });
      this.hwid = this.model;
    }
    this.hwidProgress = false;
  }

  afterFirmwareUpdate = (results?: CommandExecutionResults[]) => {
    this.firmwareProgress = false;
    let success = 0;
    let failed = 0;
    results?.forEach((result: CommandExecutionResults) => {
      if (result.exitCode == 0) {
        success += 1;
      } else {
        failed += 1;
      }
    });

    const dialogRef = this.dialog.open(DialogFirmwareComplete, {
      data: {
        title: 'Firmware Update',
        success: success,
        failed: failed
      }
    });
  };

  onFirmwareUpdate() {
    this.firmwareProgress = true;
    this.dolosService.updateFirmware(this.selectedFirmwareVersion, this.bslMode, this.afterFirmwareUpdate);
  }

  onStartTests() {
    console.log("OnstartTests");
    this.hostTestPass = TestResult.NoResult;
    this.internalDolosSerialNumber = '';
    this.dolosSerialScan = '';
    this.dolosSerialPass = TestResult.NoResult;
    this.hostTestProgress = true;
    this.dolosService.checkDolosFromHost(this.afterDolosCheck);
    this.servodService.runDutControl('servo_uart_cmd:"cc off"', this.afterCCOff);
  }

  afterCCOff = async (result: string) => {
    await sleep(2000);  // 2 second wait for cc off to take effect
    this.servodService.runDutControl('ppvar_vbat_mv', this.afterVoltage);
    this.servodService.runDutControl('ec_system_powerstate', this.afterPowerstate);
    this.servodService.runDutControl('servo_uart_cmd:"cc on"', this.afterCCOn);
  }

  afterVoltage = (result: string) => { this.testVoltage = result }
  afterPowerstate = (result: string) => { this.testPowerstate = result }
  afterCCOn = (result: string) => { }

  afterDolosCheck = (result: TestStatus, dolosSerialNumber: string, error: string) => {
    this.hostTestProgress = false;

    if (result == TestStatus.PASS) {
      this.hostTestPass = TestResult.Pass
      setTimeout(() => {
        this.dolosSerialInput?.get(0)?.nativeElement.focus()
      }, 200);
    } else {
      this.hostTestPass = TestResult.Fail
    }
    this.internalDolosSerialNumber = dolosSerialNumber;

    console.log("host" + this.hostTestPass);
    this.hostErrorMsg = error;
  };

  isTestPass(result: TestResult) {
    return result == TestResult.Pass;
    console.log("Is test pass: " + result)
  }

  showTestResult(result: TestResult, inProgress: boolean) {
    return !inProgress && result != TestResult.NoResult;
    console.log("Show result: " + inProgress + " " + result)
  }

  dolosSerialChange(event: any) {
    if (this.dolosSerialScan.trim() != '' && this.dolosSerialScan.trim() == this.internalDolosSerialNumber.trim()) {
      this.dolosSerialPass = TestResult.Pass;
    } else {
      this.dolosSerialPass = TestResult.Fail;
    }
  }

  gpnChange(event: any) {
    if (this.gpnScan.trim() == this.selectedModel?.config.gpn.trim()) {
      this.gpnPass = TestResult.Pass;
    } else {
      this.gpnPass = TestResult.Fail;
    }
  }

  onCableProgram() {
    console.log('Cable Program');
    this.cableProgramProgress = true;
    this.dolosService.programCable(this.hwid, this.afterProgramCable);
  }

  afterProgramCable = (success: boolean, errorMessage: string) => {
    this.cableProgramProgress = false;

    if (success == false) {
      console.log("Program Failed")
      console.log(errorMessage);
    }

    const dialogRef = this.dialog.open(DialogFirmwareComplete, {
      data: {
        title: 'Cable Program',
        success: success ? 1 : 0,
        failed: success ? 0 : 1,
      }
    });
  };

  afterSerialNumberUpdate = (success: boolean, errorMessage: string) => {
    this.cableProgramProgress = false;

    if (success == false) {
      console.log("Program Failed")
      console.log(errorMessage);
    }

    const dialogRef = this.dialog.open(DialogFirmwareComplete, {
      data: {
        title: 'Serial Update',
        success: success ? 1 : 0,
        failed: success ? 0 : 1,
      }
    });
  };


  onUpdateSerialNumber() {
    console.log('Serial Number Program');
    this.cableProgramProgress = true;
    var model = String(this.selectedModel?.name)
    var serialNumber = String(this.dolosSerialInput?.get(0)?.nativeElement.value)
    this.dolosService.updateSerialNumber(model.toString(), serialNumber, this.afterSerialNumberUpdate);
  }

  onReload() {
    location.reload();
  }

  isTestVoltageANumber() {
    return !isNaN(Number(this.testVoltage));
  }

}

@Component({
  selector: 'dialog-action-complete',
  templateUrl: 'dialog-action-complete.html',
  styleUrl: './dolos-provision.component.scss',
  standalone: true,
  imports: [
    MatButtonModule,
    MatDialogTitle,
    MatDialogContent,
    MatDialogActions
  ]
})

export class DialogFirmwareComplete {
  constructor(
    public dialogRef: MatDialogRef<DialogFirmwareComplete>,
    @Inject(MAT_DIALOG_DATA) public data: any
  ) { }

  onClose(): void {
    this.dialogRef.close();
  }
}

@Component({
  selector: 'dialog-oops',
  templateUrl: 'dialog-oops.html',
  styleUrl: './dolos-provision.component.scss',
  standalone: true,
  imports: [
    MatButtonModule,
    MatDialogTitle,
    MatDialogContent,
    MatDialogActions
  ]
})

export class DialogOops {
  constructor(
    public dialogRef: MatDialogRef<DialogOops>,
    @Inject(MAT_DIALOG_DATA) public data: any
  ) { }

  onClose(): void {
    this.dialogRef.close();
  }
}

async function sleep(ms: number): Promise<void> {
  return new Promise(
    (resolve) => setTimeout(resolve, ms));
}
