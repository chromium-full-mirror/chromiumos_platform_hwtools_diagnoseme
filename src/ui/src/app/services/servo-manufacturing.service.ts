// Copyright 2025 The ChromiumOS Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

import { Injectable } from '@angular/core';
import { ServoV41ManufacturingClient } from '../../proto/servo-manufacturing.pbsc';
import { Empty } from '@ngx-grpc/well-known-types';
import { StatusResponse, ProgramGenesysHubRequest, ProgramGenesysHubResponse, ProgramMcuRequest, ProgramMcuResponse, ProgramEthernetRequest, ProgramEthernetResponse, RunTestsRequest, RunTestsResponse, ValidateServoSerialRequest, ValidationResponse, ValidateServoMacAddressRequest } from '../../proto/servo-manufacturing.pb';
import { Observable } from 'rxjs';

@Injectable({
  providedIn: 'root'
})
export class ServoManufacturingService {

  constructor(private client: ServoV41ManufacturingClient) { }

  getStatus(): Observable<StatusResponse> {
    return this.client.get_status(new Empty());
  }

  programGenesysHub(): Observable<ProgramGenesysHubResponse> {
    const request = new ProgramGenesysHubRequest();
    // Set any request fields if needed
    return this.client.program_genesys_hub(request);
  }

  programMcu(firmwarePath: string): Observable<ProgramMcuResponse> {
    const request = new ProgramMcuRequest();
    request.firmwarePath = firmwarePath;
    return this.client.program_mcu(request);
  }

  programEthernet(macAddress: string): Observable<ProgramEthernetResponse> {
    const request = new ProgramEthernetRequest();
    request.macAddress = macAddress;
    return this.client.program_ethernet(request);
  }

  runTests(testsToRun: string[]): Observable<RunTestsResponse> {
    const request = new RunTestsRequest();
    request.testsToRun = testsToRun;
    return this.client.run_tests(request);
  }

  validateServoSerial(serialNumber: string): Observable<ValidationResponse> {
    const request = new ValidateServoSerialRequest();
    request.serialNumber = serialNumber;
    return this.client.validate_servo_serial(request);
  }

  validateServoMacAddress(macAddress: string): Observable<ValidationResponse> {
    const request = new ValidateServoMacAddressRequest();
    request.macAddress = macAddress;
    return this.client.validate_servo_mac_address(request);
  }
}
