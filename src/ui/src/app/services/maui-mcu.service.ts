// Copyright 2026 The ChromiumOS Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

import {Injectable} from '@angular/core';
import {Empty} from '@ngx-grpc/well-known-types';
import {Observable} from 'rxjs';
import {MauiMcuServiceClient} from '../../proto/maui-manufacturing.pbsc';
import {
  MauiH2hSpiCheckRequest,
  MauiH2hSpiCheckResponse,
  MauiListFirmwareRequest,
  MauiListFirmwareResponse,
  MauiMcuFullImageProgress,
  MauiMcuFullImageRequest,
  MauiMcuUpdateProgress,
  MauiMcuUpdateRequest,
  MauiProvisionSerialRequest,
  MauiProvisionSerialResponse,
  MauiUartBridgesResponse,
  MauiUnitStatusResponse,
} from '../../proto/maui-manufacturing.pb';

/** Client of MauiMcuService (Maui v1.2, ITE IT82002 MCU). */
@Injectable({
  providedIn: 'root',
})
export class MauiMcuService {
  constructor(private client: MauiMcuServiceClient) {}

  getUnitStatus(): Observable<MauiUnitStatusResponse> {
    return this.client.getUnitStatus(new Empty());
  }

  provisionMcuSerial(
    expectedSerial = '',
  ): Observable<MauiProvisionSerialResponse> {
    const request = new MauiProvisionSerialRequest();
    request.expectedSerial = expectedSerial;
    return this.client.provisionMcuSerial(request);
  }

  /** Reads the unit serial from the H2H SPI flash through the MCU console. */
  checkH2hSpiSerial(
    serialNumber = '',
    devicePort = '',
  ): Observable<MauiH2hSpiCheckResponse> {
    const request = new MauiH2hSpiCheckRequest();
    request.serialNumber = serialNumber;
    request.devicePort = devicePort;
    return this.client.checkH2hSpiSerial(request);
  }

  listFirmware(component: string): Observable<MauiListFirmwareResponse> {
    const request = new MauiListFirmwareRequest();
    request.component = component;
    return this.client.listFirmware(request);
  }

  updateMcuApp(
    devicePort: string,
    serialNumber: string,
    fileName: string,
  ): Observable<MauiMcuUpdateProgress> {
    const request = new MauiMcuUpdateRequest();
    request.devicePort = devicePort;
    request.serialNumber = serialNumber;
    request.fileName = fileName;
    return this.client.updateMcuApp(request);
  }

  /** USB-UART bridges for full-image flashing; the default comes first. */
  listUartBridges(): Observable<MauiUartBridgesResponse> {
    return this.client.listUartBridges(new Empty());
  }

  /** Writes the whole MCU flash with itecomdbgr (erases the MCU serial). */
  updateMcuFullImage(
    uartPort: string,
    fileName: string,
    verify: boolean,
    baud: number,
  ): Observable<MauiMcuFullImageProgress> {
    const request = new MauiMcuFullImageRequest();
    request.uartPort = uartPort;
    request.fileName = fileName;
    request.verify = verify;
    request.baud = baud;
    return this.client.updateMcuFullImage(request);
  }
}
