// Copyright 2026 The ChromiumOS Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

import {Injectable} from '@angular/core';
import {MauiStation1InitialFlashingClient} from '../../proto/maui-manufacturing.pbsc';
import {Empty} from '@ngx-grpc/well-known-types';
import {
  MauiBslDiscoveryResponse,
  MauiAutoProgramMcuRequest,
  MauiProgramMcuResponse,
  MauiTopologyDiscoveryResponse,
  MauiProgramFtdiRequest,
  MauiProgramFtdiResponse,
} from '../../proto/maui-manufacturing.pb';
import {Observable} from 'rxjs';

@Injectable({
  providedIn: 'root',
})
export class MauiStation1Service {
  constructor(private client: MauiStation1InitialFlashingClient) {}

  streamBslAutoDiscovery(): Observable<MauiBslDiscoveryResponse> {
    return this.client.streamBslAutoDiscovery(new Empty());
  }

  autoProgramMcuBsl(
    devicePort = '/dev/ttyUSB0',
    fwChannel = 'stable',
  ): Observable<MauiProgramMcuResponse> {
    const request = new MauiAutoProgramMcuRequest();
    request.devicePort = devicePort;
    request.fwChannel = fwChannel;
    return this.client.autoProgramMcuBsl(request);
  }

  discoverTopologyAndSerial(): Observable<MauiTopologyDiscoveryResponse> {
    return this.client.discoverTopologyAndSerial(new Empty());
  }

  programFtdiEeprom(
    serialNumber: string,
    ftdiDevNode = '/dev/ttyUSB0',
    vid = 0x0403,
    pid = 0x6002,
    productName = 'Maui Serial Console',
  ): Observable<MauiProgramFtdiResponse> {
    const request = new MauiProgramFtdiRequest();
    request.serialNumber = serialNumber;
    request.ftdiDevNode = ftdiDevNode || '/dev/ttyUSB0';
    request.vid = vid;
    request.pid = pid;
    request.productName = productName;
    return this.client.programFtdiEeprom(request);
  }
}
