// Copyright 2026 The ChromiumOS Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

import {ComponentFixture, TestBed} from '@angular/core/testing';
import {MauiHostTestingComponent} from './maui-host-testing';
import {MauiStation2Service} from '../services/maui-station2.service';
import {MauiDeviceMgmtService} from '../services/maui-device-mgmt.service';
import {of} from 'rxjs';
import {NoopAnimationsModule} from '@angular/platform-browser/animations';
import {provideRouter} from '@angular/router';

describe('MauiHostTestingComponent', () => {
  let component: MauiHostTestingComponent;
  let fixture: ComponentFixture<MauiHostTestingComponent>;

  const mockMauiService = {
    runHostOnlyTests: jasmine.createSpy().and.returnValue(
      of({
        allPassed: true,
        summary: 'OK',
        mcuVersionStr: 'v1',
        pdcAppconfigVer: '00000318',
      }),
    ),
    testHostReplug: jasmine
      .createSpy()
      .and.returnValue(of({allPassed: true, message: 'Replug OK'})),
    testResetButton: jasmine.createSpy().and.returnValue(
      of({
        success: true,
        uptimeSufficient: true,
        resetVerified: true,
        currentUptimeMs: 500,
      }),
    ),
    testBslButton: jasmine
      .createSpy()
      .and.returnValue(of({success: true, peakBslMv: 3200, message: 'BSL OK'})),
  };

  const mockDeviceMgmtService = {
    readConnectedDeviceSerial: jasmine.createSpy().and.returnValue(
      of({
        isConnected: true,
        serialNumber: 'TEST_MAUI_001',
        devicePort: '/dev/ttyUSB0',
      }),
    ),
    saveCardLog: jasmine
      .createSpy()
      .and.returnValue(of({success: true, message: 'Saved'})),
  };

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [MauiHostTestingComponent, NoopAnimationsModule],
      providers: [
        provideRouter([]),
        {provide: MauiStation2Service, useValue: mockMauiService},
        {provide: MauiDeviceMgmtService, useValue: mockDeviceMgmtService},
      ],
    }).compileComponents();

    fixture = TestBed.createComponent(MauiHostTestingComponent);
    component = fixture.componentInstance;
    fixture.detectChanges();
  });

  it('should create', () => {
    expect(component).toBeTruthy();
  });
});
