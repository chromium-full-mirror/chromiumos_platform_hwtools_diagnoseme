// Copyright 2025 The ChromiumOS Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

import { Injectable } from '@angular/core';
import { ServoV41ManufacturingServiceClient } from '../../proto/servo-manufacturing.pbsc';
import { Empty } from '@ngx-grpc/well-known-types';
import { StatusResponse, ProgramGenesysHubRequest, ProgramGenesysHubResponse, ProgramMcuRequest, ProgramMcuResponse, ProgramEthernetRequest, ProgramEthernetResponse, RunTestsRequest, RunTestsResponse } from '../../proto/servo-manufacturing.pb';
import { Observable } from 'rxjs';

@Injectable({
  providedIn: 'root'
})
export class ServoManufacturingService {

  constructor(private client: ServoV41ManufacturingServiceClient) { }

  getStatus(): Observable<StatusResponse> {
    return this.client.getStatus(new Empty());
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

  programEthernet(macAddress: string): Observable<ProgramEthernetResponse> {
    const request = new ProgramEthernetRequest();
    request.macAddress = macAddress;
    return this.client.programEthernet(request);
  }

  runTests(testsToRun: string[]): Observable<RunTestsResponse> {
    const request = new RunTestsRequest();
    request.testsToRun = testsToRun;
    return this.client.runTests(request);
  }
}
