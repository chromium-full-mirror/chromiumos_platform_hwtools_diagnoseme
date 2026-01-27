// Copyright 2025 The ChromiumOS Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

import {
  Component,
  ElementRef,
  Inject,
  QueryList,
  ViewChild,
  ViewChildren,
} from '@angular/core';
import {TestStatus, BoardModel} from '../../proto/diagnoseme-dolos.pb';
import {DolosService} from '../services/dolos.service';
import {FormsModule} from '@angular/forms';

import {MatIconModule} from '@angular/material/icon';
import {MatButtonModule} from '@angular/material/button';
import {MatCardModule} from '@angular/material/card';
import {MatFormFieldModule} from '@angular/material/form-field';
import {MatSelectModule} from '@angular/material/select';
import {MatToolbarModule} from '@angular/material/toolbar';
import {CommonModule} from '@angular/common';
import {
  MAT_DIALOG_DATA,
  MatDialog,
  MatDialogActions,
  MatDialogContent,
  MatDialogRef,
  MatDialogTitle,
} from '@angular/material/dialog';
import {MatInputModule} from '@angular/material/input';

import {CommandExecutionResults} from '../../proto/diagnoseme-dolos.pb';
import {ServodService} from '../services/servod-service';
import {MatCheckboxModule} from '@angular/material/checkbox';
import {DiagnosemeReworkCard} from '../diagnoseme-rework-card/diagnoseme-rework-card';
import {DiagnosemeReworkCardColumn} from '../diagnoseme-rework-card-column/diagnoseme-rework-card-column';
import {HelpDialogButton} from '../help-dialog-button/help-dialog-button';

import {NgxMatSelectSearchModule} from 'ngx-mat-select-search';
import {TestPassFailButton} from '../test-pass-fail-button/test-pass-fail-button';

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
  selector: 'dolos-provision',
  templateUrl: './dolos-provision.html',
  styleUrl: './dolos-provision.scss',
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
    DiagnosemeReworkCard,
    DiagnosemeReworkCardColumn,
    HelpDialogButton,
    NgxMatSelectSearchModule,
    TestPassFailButton,
  ],
})
export class DolosProvisionComponent {
  @ViewChildren('gpn') gpnScanInput?: QueryList<ElementRef>;
  @ViewChildren('dolosserial') dolosSerialInput?: QueryList<ElementRef>;

  @ViewChild('ecpassfail') ecPassFail!: TestPassFailButton;
  @ViewChild('dolospassfail') dolosPassFail!: TestPassFailButton;
  @ViewChild('appassfail') apPassFail!: TestPassFailButton;
  @ViewChild('serialpassfail') serialPassFail!: TestPassFailButton;

  public TestResultEnum = TestResult;

  selectedModel: Model | null = null;

  hostTestProgress = false;
  firmwareProgress = false;
  cableProgramProgress = false;
  flashSerialProgress = false;
  disableAll = false;
  hwidProgress = false;
  bslMode = false;

  model = '';
  board = '';

  testVoltage = 0;
  testPowerstate = '';
  testVoltageTestRun = false;

  dolosSerialScan = '';
  gpnScan = '';

  dutErrorMsg = '';
  hostErrorMsg = '';

  configs: CableConfig[] = [
    {name: 'config0', gpn: ''},
    {name: 'config1', gpn: ''},
    {name: 'config2', gpn: '600-02523-00'},
    {name: 'config3', gpn: '600-02524-00'},
    {name: 'config4', gpn: ''},
    {name: 'config5', gpn: ''},
    {name: 'config6', gpn: ''},
    {name: 'config7', gpn: '600-02522-00'},
  ];

  models: Model[] = [];
  filteredModels: Model[] = [];
  boardModels: Map<string, string> = new Map();
  hwid = '';

  firmwareVersions: string[] = [];
  selectedFirmwareVersion = '';
  defaultFirmwareVersion = '';

  internalDolosSerialNumber = '';
  gpnPass: TestResult = TestResult.NoResult;

  showCableReflash = false;

  eepromData = '';

  constructor(
    public dialog: MatDialog,
    private dolosService: DolosService,
    private servodService: ServodService,
  ) {
    this.dolosService.getModels(this.updateModels);
    this.dolosService.getFirmwareVersions(this.updateFirmwareVersions);
    this.dolosService.getBoardModelMapping(this.updateBoardModelMapping);
  }

  updateBoardModelMapping = (mapping: BoardModel[]) => {
    this.boardModels.clear();
    mapping.forEach((boardModel: BoardModel) => {
      this.boardModels.set(boardModel.model, boardModel.board);
    });
  };

  updateModels = (models: string[]) => {
    this.models = [];
    this.filteredModels = [];
    models.forEach((model: string) => {
      this.models.push({name: model, config: this.configs[2]});
    });
    this.models.sort((a, b) =>
      a.name > b.name ? 1 : b.name > a.name ? -1 : 0,
    );
    this.filteredModels = this.models;
  };

  updateFirmwareVersions = (
    firmwareVersions: string[],
    defaultFirmwareVersion: string,
  ) => {
    this.firmwareVersions = firmwareVersions;
    this.defaultFirmwareVersion = defaultFirmwareVersion;
    this.selectedFirmwareVersion = defaultFirmwareVersion;
  };

  onProbeHardware() {
    this.hwidProgress = true;
    this.servodService.startServod(
      this.board,
      this.model,
      this.getHWIDAfterServoStart,
    );
  }

  onChangeModel(newValue: Model) {
    this.selectedModel = newValue;
    this.gpnPass = TestResult.NoResult;
    this.gpnScan = '';
    setTimeout(() => {
      this.gpnScanInput?.get(0)?.nativeElement.focus();
    }, 10);
    this.model = this.selectedModel?.name;
    if (this.model === undefined) {
      this.model = 'unknown';
    }
    const board = this.boardModels.get(this.model);
    if (board === undefined) {
      this.board = 'unknown';
    } else {
      this.board = board;
    }
  }

  servoStart = (started: boolean, console_output: string) => {
    console.log(started);
    if (started !== true) {
      console.log(console_output);
      this.dialog.open(DialogOops, {
        data: console_output,
      });
    }
  };

  getHWIDAfterServoStart = (started: boolean, console_output: string) => {
    console.log(started);
    if (started === true) {
      this.servodService.getHWID(this.updateHWID);
    } else {
      this.hwidProgress = false;
      console.log(console_output);
      this.dialog.open(DialogOops, {
        data: console_output,
      });
    }
  };

  updateHWID = (hwid: string, output: string) => {
    console.log(output);
    if (hwid !== '') {
      this.hwid = hwid;
    } else {
      this.dialog.open(DialogOops, {
        data: output,
      });
      this.hwid = this.model;
    }
    this.hwidProgress = false;
  };

  afterFirmwareUpdate = (results?: CommandExecutionResults[]) => {
    this.firmwareProgress = false;
    let success = 0;
    let failed = 0;
    results?.forEach((result: CommandExecutionResults) => {
      if (result.exitCode === 0) {
        success += 1;
      } else {
        failed += 1;
      }
    });

    this.dialog.open(DialogFirmwareComplete, {
      data: {
        title: 'Firmware Update',
        success: success,
        failed: failed,
      },
    });
  };

  onFirmwareUpdate() {
    this.firmwareProgress = true;
    void this.dolosService.updateFirmware(
      this.selectedFirmwareVersion,
      this.bslMode,
      this.afterFirmwareUpdate,
    );
  }

  onStartTests() {
    this.servodService.startServod(this.board, this.model, this.servoStart);

    this.internalDolosSerialNumber = '';
    this.dolosSerialScan = '';
    this.hostTestProgress = true;

    this.ecPassFail.showWorking();
    this.apPassFail.showWorking();
    this.dolosPassFail.showWorking();

    setTimeout(() => {
      this.dolosSerialInput?.get(0)?.nativeElement.focus();
    }, 200);

    this.dolosService.checkDolosFromHost(this.afterDolosCheck);
    this.servodService.runDutControl(
      'servo_uart_cmd:"cc off"',
      this.afterCCOff,
    );
  }

  afterCCOff = async () => {
    await sleep(2000); // 2 second wait for cc off to take effect
    this.servodService.runDutControl('ppvar_vbat_mv', this.afterVoltage);
  };

  afterVoltage = (result: string) => {
    this.testVoltage = +result;
    this.testVoltageTestRun = true;
    if (this.testVoltage > 0) {
      this.ecPassFail.showPassed();
    } else {
      this.ecPassFail.showFailed();
    }
    this.servodService.runDutControl(
      'ec_system_powerstate',
      this.afterPowerstate,
    );
  };
  afterPowerstate = (result: string) => {
    this.testPowerstate = result;
    if (this.testPowerstate === 'S0') {
      this.apPassFail.showPassed();
    } else {
      this.apPassFail.showFailed();
    }
    this.servodService.runDutControl('servo_uart_cmd:"cc on"', this.afterCCOn);
  };
  afterCCOn = () => {
    this.hostTestProgress = false;
  };

  afterDolosCheck = (
    result: TestStatus,
    dolosSerialNumber: string,
    error: string,
  ) => {
    if (result === TestStatus.PASS) {
      this.dolosPassFail.showPassed();
    } else {
      this.dolosPassFail.showFailed();
    }
    this.internalDolosSerialNumber = dolosSerialNumber;

    this.hostErrorMsg = error;
  };

  dolosSerialChange() {
    if (
      this.dolosSerialScan.trim() !== '' &&
      this.dolosSerialScan.trim() === this.internalDolosSerialNumber.trim()
    ) {
      this.serialPassFail.showPassed();
      this.showCableReflash = false;
    } else {
      this.serialPassFail.showFailed();
      this.showCableReflash = true;
    }
  }

  gpnChange() {
    if (this.gpnScan.trim() === this.selectedModel?.config.gpn.trim()) {
      this.gpnPass = TestResult.Pass;
    } else {
      this.gpnPass = TestResult.Fail;
    }
  }

  onCableProgram() {
    console.log('Cable Program');
    this.cableProgramProgress = true;
    void this.dolosService.programCable(
      this.hwid,
      this.eepromData,
      this.afterProgramCable,
    );
  }

  afterProgramCable = (success: boolean, errorMessage: string) => {
    this.cableProgramProgress = false;

    if (success === false) {
      console.log('Program Failed');
      console.log(errorMessage);
    }

    this.dialog.open(DialogFirmwareComplete, {
      data: {
        title: 'Cable Program',
        success: success ? 1 : 0,
        failed: success ? 0 : 1,
      },
    });
  };

  afterSerialNumberUpdate = (success: boolean, errorMessage: string) => {
    this.hostTestProgress = false;

    if (success === false) {
      console.log('Program Failed');
      console.log(errorMessage);
    }

    this.dialog.open(DialogFirmwareComplete, {
      data: {
        title: 'Serial Update',
        success: success ? 1 : 0,
        failed: success ? 0 : 1,
      },
    });
  };

  onUpdateSerialNumber() {
    console.log('Serial Number Program');
    this.hostTestProgress = true;
    const serialNumber = String(
      this.dolosSerialInput?.get(0)?.nativeElement.value,
    );
    void this.dolosService.updateSerialNumber(
      this.hwid,
      this.eepromData,
      serialNumber,
      this.afterSerialNumberUpdate,
    );
  }

  onReload() {
    location.reload();
  }

  isTestVoltageANumber() {
    return !isNaN(Number(this.testVoltage));
  }

  filterModels(filterValue: string) {
    filterValue = filterValue.trim().toLowerCase();
    this.filteredModels = this.models.filter(model =>
      model.name.toLowerCase().startsWith(filterValue),
    );
  }

  onEepromDataFileSelected(event: Event): void {
    const input = event.target as HTMLInputElement;
    if (input.files && input.files.length > 0) {
      const selectedFile = input.files[0];
      console.log('Selected file:', selectedFile.name);
      const reader = new FileReader();
      reader.onload = (e: ProgressEvent<FileReader>) => {
        if (e.target && e.target.result) {
          this.eepromData = e.target.result as string;
        }
        console.log(this.eepromData);
      };
      reader.onerror = () => {
        console.error('Error reading file:', reader.error);
        this.eepromData = 'Error reading file.';
      };
      reader.readAsText(selectedFile);
    }
  }
}

@Component({
  selector: 'dialog-action-complete',
  templateUrl: './dialog-action-complete.html',
  styleUrl: './dolos-provision.scss',
  standalone: true,
  imports: [
    MatButtonModule,
    MatDialogTitle,
    MatDialogContent,
    MatDialogActions,
  ],
})
export class DialogFirmwareComplete {
  constructor(
    public dialogRef: MatDialogRef<DialogFirmwareComplete>,
    @Inject(MAT_DIALOG_DATA) public data: any,
  ) {}

  onClose(): void {
    this.dialogRef.close();
  }
}

@Component({
  selector: 'dialog-oops',
  templateUrl: 'dialog-oops.html',
  styleUrl: './dolos-provision.scss',
  standalone: true,
  imports: [
    MatButtonModule,
    MatDialogTitle,
    MatDialogContent,
    MatDialogActions,
  ],
})
export class DialogOops {
  constructor(
    public dialogRef: MatDialogRef<DialogOops>,
    @Inject(MAT_DIALOG_DATA) public data: any,
  ) {}

  onClose(): void {
    this.dialogRef.close();
  }
}

async function sleep(ms: number): Promise<void> {
  return new Promise(resolve => setTimeout(resolve, ms));
}
