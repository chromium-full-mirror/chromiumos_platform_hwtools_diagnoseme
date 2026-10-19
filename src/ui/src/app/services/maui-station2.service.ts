// Copyright 2026 The ChromiumOS Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

import {Injectable} from '@angular/core';
import {MauiStation2FunctionalTestingClient} from '../../proto/maui-manufacturing.pbsc';
import {
  MauiHostOnlyTestRequest,
  MauiHostOnlyTestResponse,
  MauiHostReplugTestRequest,
  MauiHostReplugTestResponse,
  MauiResetButtonTestRequest,
  MauiResetButtonTestResponse,
  MauiBslButtonTestRequest,
  MauiBslButtonTestResponse,
  MauiBslButtonProgress,
  MauiHostDutTestRequest,
  MauiHostDutTestResponse,
  MauiAdbTransferTestRequest,
  MauiAdbTransferTestResponse,
} from '../../proto/maui-manufacturing.pb';
import {Observable} from 'rxjs';

@Injectable({
  providedIn: 'root',
})
export class MauiStation2Service {
  constructor(private client: MauiStation2FunctionalTestingClient) {}

  runHostOnlyTests(
    serialNumber = '',
    devicePort = '',
  ): Observable<MauiHostOnlyTestResponse> {
    const request = new MauiHostOnlyTestRequest();
    request.serialNumber = serialNumber;
    request.devicePort = devicePort;
    return this.client.runHostOnlyTests(request);
  }

  testHostReplug(
    serialNumber = '',
    devicePort = '',
    previousOrientation = '',
  ): Observable<MauiHostReplugTestResponse> {
    const request = new MauiHostReplugTestRequest();
    request.serialNumber = serialNumber;
    request.devicePort = devicePort;
    request.previousOrientation = previousOrientation;
    return this.client.testHostReplug(request);
  }

  testResetButton(
    serialNumber = '',
    devicePort = '',
    action = 'check_initial_uptime',
    initialUptimeMs = 0,
  ): Observable<MauiResetButtonTestResponse> {
    const request = new MauiResetButtonTestRequest();
    request.serialNumber = serialNumber;
    request.devicePort = devicePort;
    request.action = action;
    request.initialUptimeMs = String(initialUptimeMs);
    return this.client.testResetButton(request);
  }

  testBslButton(
    serialNumber = '',
    devicePort = '',
    maxDurationSec = 5,
  ): Observable<MauiBslButtonTestResponse> {
    const request = new MauiBslButtonTestRequest();
    request.serialNumber = serialNumber;
    request.devicePort = devicePort;
    request.maxSampleDurationSec = maxDurationSec;
    return this.client.testBslButton(request);
  }

  /**
   * Guided 3-phase BSL button check (Maui v1.2): streams the live prompt
   * and countdown; the last message has done set.
   */
  streamBslButtonTest(
    serialNumber = '',
    devicePort = '',
  ): Observable<MauiBslButtonProgress> {
    const request = new MauiBslButtonTestRequest();
    request.serialNumber = serialNumber;
    request.devicePort = devicePort;
    return this.client.streamBslButtonTest(request);
  }

  runHostDutTests(
    serialNumber = '',
    expectedSerial = '',
    stage = 'all',
    previousOrientation = '',
  ): Observable<MauiHostDutTestResponse> {
    const request = new MauiHostDutTestRequest();
    request.serialNumber = serialNumber;
    request.expectedSerial = expectedSerial;
    request.stage = stage;
    request.previousOrientation = previousOrientation;
    return this.client.runHostDutTests(request);
  }

  /** Pushes/pulls two ~400 MB random files to the DUT over ADB (40 s max). */
  runAdbTransferTest(
    serialNumber = '',
    adbSerial = '',
    sizeMb = 0,
  ): Observable<MauiAdbTransferTestResponse> {
    const request = new MauiAdbTransferTestRequest();
    request.serialNumber = serialNumber;
    request.adbSerial = adbSerial;
    request.sizeMb = sizeMb;
    return this.client.runAdbTransferTest(request);
  }
}
