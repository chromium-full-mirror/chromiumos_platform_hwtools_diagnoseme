// Copyright 2025 The ChromiumOS Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

import { Component, ElementRef, HostListener, Inject, QueryList, ViewChildren } from '@angular/core';
import { TestStatus } from 'src/protos/diagnoseme-dolos.pb';
import { DolosService } from '../services/dolos.service';
import {NgIf, TitleCasePipe} from '@angular/common'
import {FormsModule} from '@angular/forms'

import { MatIconModule } from '@angular/material/icon';
import { MatButtonModule } from '@angular/material/button';
import { MatCardModule } from '@angular/material/card';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatSelectModule } from '@angular/material/select';
import { MatToolbarModule } from '@angular/material/toolbar';
import { MatProgressBarModule } from '@angular/material/progress-bar';

import {
  MAT_DIALOG_DATA,
  MatDialog,
  MatDialogActions,
  MatDialogClose,
  MatDialogContent,
  MatDialogRef,
  MatDialogTitle
} from '@angular/material/dialog';

import { CommandExecutionResults } from 'src/protos/diagnoseme-dolos.pb';


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
      NgIf,
      TitleCasePipe,
      MatIconModule,
      MatCardModule,
      MatFormFieldModule,
      MatSelectModule,
      MatToolbarModule,
      MatProgressBarModule,
      FormsModule,
  ]
})

export class DolosProvisionComponent {

  @ViewChildren("gpn") gpnScanInput?: QueryList<ElementRef>;
  @ViewChildren("dolosserial") dolosSerialInput?: QueryList<ElementRef>;

  public TestResultEnum = TestResult;

  selectedModel: Model | null = null;

  dutTestProgress: boolean = false;
  hostTestProgress: boolean = false;
  firmwareProgress: boolean = false;
  cableProgramProgress: boolean = false;
  flashSerialProgress: boolean = false;
  disableAll: boolean = false;


  dutTestPass: TestResult = TestResult.NoResult;
  hostTestPass: TestResult = TestResult.NoResult;
  dolosSerialPass: TestResult = TestResult.NoResult;

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

  models: Model[] = [
    { name: 'careena', config: this.configs[2] },
    { name: 'vayne', config: this.configs[2] },
    { name: 'blooglet', config: this.configs[2] },
    { name: 'bloog', config: this.configs[2] },
    { name: 'vortininja', config: this.configs[2] },
    { name: 'blooguard', config: this.configs[2] },
    { name: 'meep', config: this.configs[2] },
    { name: 'vorticon', config: this.configs[2] },
    { name: 'taniks', config: this.configs[3] },
    { name: 'whitetip', config: this.configs[3] },
    { name: 'akemi', config: this.configs[3] },
    { name: 'lick', config: this.configs[3] },
    { name: 'laser14', config: this.configs[3] },
    { name: 'foob', config: this.configs[3] },
    { name: 'lillipup', config: this.configs[3] },
    { name: 'cret', config: this.configs[7] },
    { name: 'aleena', config: this.configs[7] },
    { name: 'kindred', config: this.configs[7] },
    { name: 'akali', config: this.configs[7] },
    { name: 'sparky', config: this.configs[7] },
    { name: 'garg', config: this.configs[7] },
    { name: 'bobba360', config: this.configs[7] },
    { name: 'electro', config: this.configs[7] },
    { name: 'limozeen', config: this.configs[7] },
    { name: 'delbin', config: this.configs[7] }
  ];

  firmwareVersions: string[] = [];
  selectedFirmwareVersion: string = "";


  internalDolosSerialNumber: string = '';
  gpnPass: TestResult = TestResult.NoResult;

  constructor(public dialog: MatDialog, private dolosService: DolosService) {
    this.dolosService.getModels(this.updateModels)
    this.models.sort((a, b) =>
      a.name > b.name ? 1 : b.name > a.name ? -1 : 0
    );
    this.dolosService.getFirmwareVersions(this.updateFirmwareVersions)
  }

  updateModels = (models: string[]) => {
    this.models = []
    models.forEach((model: string) => {
      this.models.push({name: model, config: this.configs[2]})
    })
  }

  updateFirmwareVersions = (firmwareVersions: string[], defaultFirmwareVersion:string) => {
    this.firmwareVersions = firmwareVersions;
    this.selectedFirmwareVersion = defaultFirmwareVersion;
  }


  onChangeModel(newValue: Model) {
    console.log(newValue);
    this.selectedModel = newValue;
    this.gpnPass = TestResult.NoResult;
    this.gpnScan = '';
    console.log(this.gpnScanInput?.get(0))
    setTimeout(() => {
       this.gpnScanInput?.get(0)?.nativeElement.focus()
    }, 10);
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
    console.log('Firmware Update');
    this.firmwareProgress = true;
    this.dolosService.updateFirmware(this.selectedFirmwareVersion, this.afterFirmwareUpdate);
  }

  onBootup() {
    this.dutTestPass = TestResult.NoResult;
    this.hostTestPass = TestResult.NoResult;
    this.internalDolosSerialNumber = '';
    this.dolosSerialScan = '';
    this.dolosSerialPass = TestResult.NoResult;
    this.hostTestProgress = true;
    this.dutTestProgress = true;
    this.dolosService.checkDolosFromHost(this.afterHostCheck);
    this.dolosService.checkDolosFromDut(this.afterDutCheck);
  }

  afterHostCheck = (result: TestStatus, dolosSerialNumber: string, error: string) => {
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

  afterDutCheck = (result: TestStatus, error: string) => {
    this.dutTestProgress = false;
    if (result == TestStatus.PASS) {
      this.dutTestPass = TestResult.Pass
    } else {
      this.dutTestPass = TestResult.Fail
    }
    this.dutErrorMsg = error;
  };

  isTestPass(result: TestResult) {
    return result == TestResult.Pass;
    console.log("Is test pass: " + result)
  }

  showTestResult(result: TestResult, inProgress: boolean) {
    return !inProgress && result != TestResult.NoResult;
    console.log("Show result: " + inProgress + " " + result)
  }

  openCablePics() {
    const dialogRef = this.dialog.open(DialogOverviewExampleDialog, {
      data: this.selectedModel?.config.gpn + ".jpg"
    });
  }

  openRoutePics() {
  }

  openConnectPics() {
    const dialogRef = this.dialog.open(DialogOverviewExampleDialog, {
      data: "connect_motherboard.jpg"
    });
  }

  openBatteryPics() {
  }

  openScrewsPics() {
    const dialogRef = this.dialog.open(DialogOverviewExampleDialog, {
      data: "remove_screws.jpg"
    });
  }

  openConnectDolosPics() {
    const dialogRef = this.dialog.open(DialogOverviewExampleDialog, {
      data: "dolos.png"
    });
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

  @HostListener('window:keydown.control.r', ['$event'])
  runTests(event: KeyboardEvent) {
    event.preventDefault();
    this.onBootup();
  }

  @HostListener('window:keydown.control.f', ['$event'])
  updateFirmware(event: KeyboardEvent) {
    event.preventDefault();
    this.onFirmwareUpdate();
  }

  onCableProgram() {
    console.log('Cable Program');
    this.cableProgramProgress = true;
    var model = String(this.selectedModel?.name)
    this.dolosService.programCable(model.toString(), this.afterProgramCable);
  }

  afterProgramCable = (result?: CommandExecutionResults) => {
    this.cableProgramProgress = false;
    let success = 0;
    let failed = 0;

    if (result && result.exitCode == 0) {
      success += 1;
    } else {
      failed += 1;
    }
    if (result) {
      console.log(result.stderr);
      console.log(result.stdout);
    }

    const dialogRef = this.dialog.open(DialogFirmwareComplete, {
      data: {
        title: 'Cable Program',
        success: success,
        failed: failed
      }
    });
  };

    afterSerialNumberUpdate = (result?: CommandExecutionResults) => {
    this.flashSerialProgress = false;
    let success = 0;
    let failed = 0;

    if (result && result.exitCode == 0) {
      success += 1;
    } else {
      failed += 1;
    }
    if (result) {
      console.log(result.stderr);
      console.log(result.stdout);
    }

    const dialogRef = this.dialog.open(DialogFirmwareComplete, {
      data: {
        title: 'Serial Update',
        success: success,
        failed: failed
      }
    });
  };


  onUpdateSerialNumber() {
    console.log('Serial Number Program');
    this.flashSerialProgress = true;
    var model = String(this.selectedModel?.name)
    var serialNumber = String(this.dolosSerialInput?.get(0)?.nativeElement.value)
    this.dolosService.updateSerialNumber(model.toString(), serialNumber, this.afterSerialNumberUpdate);
  }

  onReload() {
    location.reload();
  }

}

@Component({
  selector: 'dialog-help-picture',
  templateUrl: './dialog-help-picture.html',
  styleUrl: './dolos-provision.component.scss',
  standalone: true,
  imports: [MatDialogContent, MatDialogActions, MatDialogClose, MatButtonModule]
})
export class DialogOverviewExampleDialog {
  constructor(
    public dialogRef: MatDialogRef<DialogOverviewExampleDialog>,
    @Inject(MAT_DIALOG_DATA) public data: any
  ) {
    console.log(data);
  }

  onClose(): void {
    this.dialogRef.close();
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
    MatDialogActions,
    MatDialogClose
  ]
})
export class DialogFirmwareComplete {
  constructor(
    public dialogRef: MatDialogRef<DialogFirmwareComplete>,
    @Inject(MAT_DIALOG_DATA) public data: any
  ) {}

  onClose(): void {
    this.dialogRef.close();
  }
}
