// Copyright 2026 The ChromiumOS Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

import {Injectable} from '@angular/core';
import {ServoMicroManufacturingClient} from '../../proto/servo-manufacturing.pbsc';
import {Empty} from '@ngx-grpc/well-known-types';
import {
  StatusResponse,
  ProgramMcuRequest,
  ProgramMcuResponse,
  ProgramSerialRequest,
  ProgramSerialResponse,
  RunTestsRequest,
  RunTestsResponse,
  ValidateServoSerialRequest,
  ValidationResponse,
  MicroDevicePresenceResponse,
  SubmitMicroProvisioningResultsRequest,
  SubmitProvisioningResultsResponse,
} from '../../proto/servo-manufacturing.pb';
import {Observable} from 'rxjs';

@Injectable({
  providedIn: 'root',
})
export class ServoMicroManufacturingService {
  constructor(private client: ServoMicroManufacturingClient) {}

  getStatus(): Observable<StatusResponse> {
    return this.client.getStatus(new Empty());
  }

  getDevicePresence(): Observable<MicroDevicePresenceResponse> {
    return this.client.getDevicePresence(new Empty());
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

  runTests(serialNumber: string): Observable<RunTestsResponse> {
    const request = new RunTestsRequest();
    request.serialNumber = serialNumber;
    return this.client.runTests(request);
  }

  validateServoSerial(serialNumber: string): Observable<ValidationResponse> {
    const request = new ValidateServoSerialRequest();
    request.serialNumber = serialNumber;
    return this.client.validateServoSerial(request);
  }

  submitProvisioningResults(
    request: SubmitMicroProvisioningResultsRequest,
  ): Observable<SubmitProvisioningResultsResponse> {
    return this.client.submitProvisioningResults(request);
  }
}
