// Copyright 2025 The ChromiumOS Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

import { Injectable, isDevMode } from '@angular/core';
import { CommandExecutionResults, TestStatus, BoardModel } from '../../proto/diagnoseme-dolos.pb';
import { DolosRpcServiceClient } from '../../proto/diagnoseme-dolos.pbsc';

import { Empty } from '@ngx-grpc/well-known-types';
import { FirmwareUpdateRequest, ProgramCableRequest, ProgramSerialRequest } from '../../proto/diagnoseme-dolos.pb';

@Injectable({
  providedIn: 'root'
})
export class DolosService {
  constructor(private client: DolosRpcServiceClient) {}

  async updateFirmware(
    firmwareVersion: string,
    callbackfn: (results: CommandExecutionResults[] | undefined) => void
  ) {
    if (isDevMode()) {
      let result: CommandExecutionResults = new CommandExecutionResults();
      result.exitCode = 0;
      let results: CommandExecutionResults[] = [];
      results.push(result);
      await this.sleep(10000);
      callbackfn(results);
    } else {
      let request: FirmwareUpdateRequest = new FirmwareUpdateRequest();
      request.firmwareVersion = firmwareVersion;

      this.client.updateFirmware(request).subscribe(res => {
        let results: CommandExecutionResults[] | undefined = res.result;
        callbackfn(results);
      });
    }
  }

  checkDolosFromHost(
    callbackfn: (
      result: TestStatus,
      dolosSerialNumber: string,
      error: string
    ) => void
  ) {
    if (isDevMode()) {
      callbackfn(
        TestStatus.PASS,
        'dolos-V1-2415-000A',
        'This is a host error'
      );
    } else {
      this.client.checkDolosFromHost(new Empty()).subscribe(res => {
        callbackfn(res.status, res.dolosSerialNumber, res.error);
      });
    }
  }

  checkDolosFromDut(callbackfn: (result: TestStatus, error: string) => void) {
    if (isDevMode()) {
      callbackfn(TestStatus.PASS, 'This is a DUT error');
    } else {
      this.client.checkDolosFromDut(new Empty()).subscribe(res => {
        callbackfn(res.status, res.error);
      });
    }
  }

  async programCable(model: string,
    callbackfn: (results: CommandExecutionResults | undefined) => void) {
    if (isDevMode()) {
      let result: CommandExecutionResults = new CommandExecutionResults();
      result.exitCode = 0;
      await this.sleep(10000);
      callbackfn(result);
    } else {
      var request = new ProgramCableRequest()
      request.model = model;
      this.client.programCable(request).subscribe(res => {
        callbackfn(res.result);
      });
    }
  }

  getModels(callbackfn: (models: string[] ) => void) {
    if (isDevMode()) {
      callbackfn(["foob360", "foob"]);
    } else {
      this.client.getModels(new Empty()).subscribe(res => {
        callbackfn(res.models);
      });
    }
  }

  async getBoardModelMapping(callbackfn: (mapping: BoardModel[] ) => void) {
      this.client.getBoardModelMapping(new Empty()).subscribe(res => {
        if (res.boardModelMappings == undefined) {callbackfn([])} else {callbackfn(res.boardModelMappings)};
      });
  }

  getFirmwareVersions(callbackfn: (versions: string[], default_version: string ) => void) {
    if (isDevMode()) {
      callbackfn(["1", "2", "3"], "2");
    } else {
      this.client.getFirmwareVersions(new Empty()).subscribe(res => {
        callbackfn(res.firmwareVersion, res.defaultFirmwareVersion);
      });
    }
  }

  async updateSerialNumber(model: string, serialNumber: string, callbackfn: (results: CommandExecutionResults | undefined) => void) {
    if (isDevMode()) {
      let result: CommandExecutionResults = new CommandExecutionResults();
      result.exitCode = 0;
      await this.sleep(10000);
      callbackfn(result);
    } else {
      var request = new ProgramSerialRequest()
      request.model = model;
      request.newSerialNumber = serialNumber;
      this.client.programSerialNumber(request).subscribe(res => {
        callbackfn(res.result);
      });
    }
  }

  sleep(ms: number) {
    return new Promise(resolve => setTimeout(resolve, ms));
  }
}
