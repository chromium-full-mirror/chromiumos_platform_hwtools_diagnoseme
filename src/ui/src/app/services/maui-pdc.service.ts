// Copyright 2026 The ChromiumOS Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

import {Injectable} from '@angular/core';
import {MauiPdcFlashingClient} from '../../proto/maui-manufacturing.pbsc';
import {Empty} from '@ngx-grpc/well-known-types';
import {
  MauiConnectedPdcDevicesResponse,
  MauiProgramSinglePdcRequest,
  MauiProgramPdcProgressResponse,
} from '../../proto/maui-manufacturing.pb';
import {Observable} from 'rxjs';

@Injectable({
  providedIn: 'root',
})
export class MauiPdcService {
  constructor(private client: MauiPdcFlashingClient) {}

  discoverConnectedPdcDevices(): Observable<MauiConnectedPdcDevicesResponse> {
    return this.client.discoverConnectedPdcDevices(new Empty());
  }

  programSinglePdc(
    slotIndex: number,
    serialNumber: string,
    devicePort: string,
    fwChannel = 'v20_0325_100W',
  ): Observable<MauiProgramPdcProgressResponse> {
    const request = new MauiProgramSinglePdcRequest();
    request.slotIndex = slotIndex;
    request.serialNumber = serialNumber;
    request.devicePort = devicePort;
    request.fwChannel = fwChannel;
    return this.client.programSinglePdc(request);
  }
}
