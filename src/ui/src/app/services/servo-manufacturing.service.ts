// Copyright 2026 The ChromiumOS Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

import {Injectable} from '@angular/core';
import {ServoV41ManufacturingClient} from '../../proto/servo-manufacturing.pbsc';
import {Empty} from '@ngx-grpc/well-known-types';
import {
  StatusResponse,
  ProgramGenesysHubRequest,
  ProgramGenesysHubResponse,
  ProgramMcuRequest,
  ProgramMcuResponse,
  ProgramSerialRequest,
  ProgramSerialResponse,
  ProgramEthernetRequest,
  ProgramEthernetResponse,
  ProgramAtmegaRequest,
  ProgramAtmegaResponse,
  RunTestsRequest,
  RunTestsResponse,
  ValidateServoSerialRequest,
  ValidationResponse,
  ValidateServoMacAddressRequest,
  DevicePresenceResponse,
  SubmitProvisioningResultsRequest,
  SubmitProvisioningResultsResponse,
} from '../../proto/servo-manufacturing.pb';
import {Observable} from 'rxjs';

@Injectable({
  providedIn: 'root',
})
export class ServoManufacturingService {
  constructor(private client: ServoV41ManufacturingClient) {}

  getStatus(): Observable<StatusResponse> {
    return this.client.getStatus(new Empty());
  }

  getDevicePresence(): Observable<DevicePresenceResponse> {
    return this.client.getDevicePresence(new Empty());
  }

  programGenesysHub(): Observable<ProgramGenesysHubResponse> {
    const request = new ProgramGenesysHubRequest();
    // Set any request fields if needed
    return this.client.programGenesysHub(request);
  }

  programMcu(firmwarePath: string): Observable<ProgramMcuResponse> {
    const request = new ProgramMcuRequest();
    request.firmwarePath = firmwarePath;
    return this.client.programMcu(request);
  }

  programSerial(serialNumber: string): Observable<ProgramSerialResponse> {
    const request = new ProgramSerialRequest();
    request.serialNumber = serialNumber;
    return this.client.programSerial(request);
  }

  programEthernet(
    macAddress: string,
    serialNumber: string,
  ): Observable<ProgramEthernetResponse> {
    const request = new ProgramEthernetRequest();
    request.macAddress = macAddress;
    request.serialNumber = serialNumber;
    return this.client.programEthernet(request);
  }

  programAtmega(serialNumber: string): Observable<ProgramAtmegaResponse> {
    const request = new ProgramAtmegaRequest();
    request.serialNumber = serialNumber;
    return this.client.programAtmega(request);
  }

  runConsoleTests(
    serialNumber: string,
    macAddress: string,
  ): Observable<RunTestsResponse> {
    const request = new RunTestsRequest();
    request.serialNumber = serialNumber;
    request.macAddress = macAddress;
    return this.client.runConsoleTests(request);
  }

  runFunctionalTests(
    serialNumber: string,
    macAddress: string,
  ): Observable<RunTestsResponse> {
    const request = new RunTestsRequest();
    request.serialNumber = serialNumber;
    request.macAddress = macAddress;
    return this.client.runFunctionalTests(request);
  }

  runIntegrationTests(
    serialNumber: string,
    macAddress: string,
  ): Observable<RunTestsResponse> {
    const request = new RunTestsRequest();
    request.serialNumber = serialNumber;
    request.macAddress = macAddress;
    return this.client.runIntegrationTests(request);
  }

  validateServoSerial(serialNumber: string): Observable<ValidationResponse> {
    const request = new ValidateServoSerialRequest();
    request.serialNumber = serialNumber;
    return this.client.validateServoSerial(request);
  }

  validateServoMacAddress(macAddress: string): Observable<ValidationResponse> {
    const request = new ValidateServoMacAddressRequest();
    request.macAddress = macAddress;
    return this.client.validateServoMacAddress(request);
  }

  submitProvisioningResults(
    request: SubmitProvisioningResultsRequest,
  ): Observable<SubmitProvisioningResultsResponse> {
    return this.client.submitProvisioningResults(request);
  }
}
