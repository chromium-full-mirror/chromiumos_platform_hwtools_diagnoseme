// Copyright 2026 The ChromiumOS Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

import {Injectable} from '@angular/core';
import {Empty} from '@ngx-grpc/well-known-types';
import {MauiDeviceManagementClient} from '../../proto/maui-manufacturing.pbsc';
import {
  MauiReadSerialResponse,
  MauiLogsStatusRequest,
  MauiLogsStatusResponse,
  MauiSaveLogRequest,
  MauiSaveLogResponse,
  MauiVerifyLabelRequest,
  MauiVerifyLabelResponse,
  MauiFinalSubmissionRequest,
  MauiFinalSubmissionResponse,
} from '../../proto/maui-manufacturing.pb';
import {Observable} from 'rxjs';

@Injectable({
  providedIn: 'root',
})
export class MauiDeviceMgmtService {
  constructor(private client: MauiDeviceManagementClient) {}

  readConnectedDeviceSerial(): Observable<MauiReadSerialResponse> {
    return this.client.readConnectedDeviceSerial(new Empty());
  }

  getDeviceLogsStatus(
    serialNumber: string,
  ): Observable<MauiLogsStatusResponse> {
    const request = new MauiLogsStatusRequest();
    request.serialNumber = serialNumber;
    return this.client.getDeviceLogsStatus(request);
  }

  saveCardLog(
    serialNumber: string,
    logStepName: string,
    logContent: string,
  ): Observable<MauiSaveLogResponse> {
    const request = new MauiSaveLogRequest();
    request.serialNumber = serialNumber;
    request.logStepName = logStepName;
    request.logContent = logContent;
    return this.client.saveCardLog(request);
  }

  verifyScannedLabel(
    electronicSerial: string,
    scannedBarcode: string,
    adbSerial = '',
    ftdiSerial = '',
  ): Observable<MauiVerifyLabelResponse> {
    const request = new MauiVerifyLabelRequest();
    request.electronicSerial = electronicSerial;
    request.scannedBarcode = scannedBarcode;
    request.adbSerial = adbSerial;
    request.ftdiSerial = ftdiSerial;
    return this.client.verifyScannedLabel(request);
  }

  submitFinalManufacturingResults(
    serialNumber: string,
    operatorId: string,
    stationId: string,
    mcuFlashed: boolean,
    ftdiProgrammed: boolean,
    pdcFlashed: boolean,
    hostOnlyPassed: boolean,
    hostDutPassed: boolean,
    labelVerified: boolean,
    stageStatuses?: {[key: string]: string},
    adbSerial = '',
    ftdiSerial = '',
    scannedBarcode = '',
  ): Observable<MauiFinalSubmissionResponse> {
    const request = new MauiFinalSubmissionRequest();
    request.serialNumber = serialNumber;
    request.operatorId = operatorId;
    request.stationId = stationId;
    request.mcuFlashed = mcuFlashed;
    request.ftdiProgrammed = ftdiProgrammed;
    request.pdcFlashed = pdcFlashed;
    request.hostOnlyPassed = hostOnlyPassed;
    request.hostDutPassed = hostDutPassed;
    request.labelVerified = labelVerified;
    if (stageStatuses) {
      request.stageStatuses = stageStatuses;
    }
    request.adbSerial = adbSerial;
    request.ftdiSerial = ftdiSerial;
    request.scannedBarcode = scannedBarcode;
    return this.client.submitFinalManufacturingResults(request);
  }
}
