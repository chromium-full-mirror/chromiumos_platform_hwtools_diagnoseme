// Copyright 2025 The ChromiumOS Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

import {Injectable, isDevMode} from '@angular/core';
import {
  CommandExecutionResults,
  TestStatus,
  BoardModel,
  BoardModelMappingResponse,
} from '../../proto/diagnoseme-dolos.pb';
import {DolosRpcServiceClient} from '../../proto/diagnoseme-dolos.pbsc';

import {Empty} from '@ngx-grpc/well-known-types';
import {
  FirmwareUpdateRequest,
  ProgramCableRequest,
} from '../../proto/diagnoseme-dolos.pb';

@Injectable({
  providedIn: 'root',
})
export class DolosService {
  constructor(private client: DolosRpcServiceClient) {}

  async updateFirmware(
    firmwareVersion: string,
    bslMode: boolean,
    callbackfn: (results: CommandExecutionResults[] | undefined) => void,
  ) {
    if (isDevMode()) {
      const result: CommandExecutionResults = new CommandExecutionResults();
      result.exitCode = 0;
      const results: CommandExecutionResults[] = [];
      results.push(result);
      await this.sleep(10000);
      callbackfn(results);
    } else {
      const request: FirmwareUpdateRequest = new FirmwareUpdateRequest();
      request.firmwareVersion = firmwareVersion;
      request.bslMode = bslMode;

      this.client.updateFirmware(request).subscribe(res => {
        const results: CommandExecutionResults[] | undefined = res.result;
        callbackfn(results);
      });
    }
  }

  checkDolosFromHost(
    callbackfn: (
      result: TestStatus,
      dolosSerialNumber: string,
      error: string,
    ) => void,
  ) {
    if (isDevMode()) {
      callbackfn(TestStatus.PASS, 'dolos-V1-2415-000A', 'This is a host error');
    } else {
      this.client.checkDolosFromHost(new Empty()).subscribe(res => {
        console.log(res.status);
        console.log(res.dolosSerialNumber);
        console.log(res.error);
        callbackfn(res.status, res.dolosSerialNumber, res.error);
      });
    }
  }

  async programCable(
    hwid: string,
    eepromData: string,
    callbackfn: (success: boolean, error_message: string) => void,
  ) {
    if (isDevMode()) {
      await this.sleep(10000);
      callbackfn(true, '');
    } else {
      const request = new ProgramCableRequest();
      request.hwid = hwid;
      if (eepromData !== '') {
        request.eepromData = eepromData;
      }
      this.client.programCable(request).subscribe(res => {
        console.log(res);
        callbackfn(res.success, res.errorMessage);
      });
    }
  }

  getModels(callbackfn: (models: string[]) => void) {
    if (isDevMode()) {
      callbackfn(['foob360', 'foob']);
    } else {
      this.client.getModels(new Empty()).subscribe(res => {
        callbackfn(res.models);
      });
    }
  }

  getBoardModelMapping(callbackfn: (mapping: BoardModel[]) => void) {
    this.client
      .getBoardModelMapping(new Empty())
      .subscribe((res: BoardModelMappingResponse) => {
        if (res.boardModelMappings === undefined) {
          callbackfn([]);
        } else {
          callbackfn(res.boardModelMappings);
        }
      });
  }

  getFirmwareVersions(
    callbackfn: (versions: string[], default_version: string) => void,
  ) {
    if (isDevMode()) {
      callbackfn(['1', '2', '3'], '2');
    } else {
      this.client.getFirmwareVersions(new Empty()).subscribe(res => {
        callbackfn(res.firmwareVersion, res.defaultFirmwareVersion);
      });
    }
  }

  async updateSerialNumber(
    hwid: string,
    eepromData: string,
    serialNumber: string,
    callbackfn: (success: boolean, error_message: string) => void,
  ) {
    if (isDevMode()) {
      const result: CommandExecutionResults = new CommandExecutionResults();
      result.exitCode = 0;
      await this.sleep(10000);
      callbackfn(true, '');
    } else {
      const request = new ProgramCableRequest();
      request.hwid = hwid;
      if (eepromData !== '') {
        request.eepromData = eepromData;
      }
      request.newSerialNumber = serialNumber;
      this.client.programCable(request).subscribe(res => {
        callbackfn(res.success, res.errorMessage);
      });
    }
  }

  sleep(ms: number) {
    return new Promise(resolve => setTimeout(resolve, ms));
  }
}
