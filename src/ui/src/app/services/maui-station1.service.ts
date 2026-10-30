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
  MauiTopologyDiscoveryRequest,
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
    serialNumber = '',
  ): Observable<MauiProgramMcuResponse> {
    const request = new MauiAutoProgramMcuRequest();
    request.devicePort = devicePort;
    request.fwChannel = fwChannel;
    request.serialNumber = serialNumber;
    return this.client.autoProgramMcuBsl(request);
  }

  discoverTopologyAndSerial(
    devicePort = '',
    containerId = '',
    ftdiSerial = '',
  ): Observable<MauiTopologyDiscoveryResponse> {
    const request = new MauiTopologyDiscoveryRequest();
    request.devicePort = devicePort;
    request.containerId = containerId;
    request.ftdiSerial = ftdiSerial;
    return this.client.discoverTopologyAndSerial(request);
  }

  programFtdiEeprom(
    serialNumber: string,
    ftdiDevNode = '/dev/ttyUSB0',
    vid = 0x0403,
    pid = 0x6002,
    productName = 'Maui Serial Console',
    oldSerialNumber = '',
    oldPid = 0,
  ): Observable<MauiProgramFtdiResponse> {
    const request = new MauiProgramFtdiRequest();
    request.serialNumber = serialNumber;
    request.ftdiDevNode = ftdiDevNode || '/dev/ttyUSB0';
    request.vid = vid;
    request.pid = pid;
    request.productName = productName;
    request.oldSerialNumber = oldSerialNumber;
    request.oldPid = oldPid;
    return this.client.programFtdiEeprom(request);
  }
}
