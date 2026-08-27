// Copyright 2026 The ChromiumOS Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

import {
  ComponentFixture,
  TestBed,
  fakeAsync,
  tick,
} from '@angular/core/testing';
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

  it('should record card1Orientation on runUsbCheck', fakeAsync(() => {
    mockMauiService.runHostOnlyTests.and.returnValue(
      of({
        allPassed: true,
        summary: 'OK',
        mcuVersionStr: 'v1',
        pdcAppconfigVer: '00000318',
        hostOrientation: 'CC1 (895mV / 58mV)',
      }),
    );
    component.runUsbCheck();
    tick(3000);
    expect(component.card1Orientation).toBe('CC1 (895mV / 58mV)');
    expect(component.hostOrientation).toBe('CC1 (895mV / 58mV)');
    expect(component.usbCheckPassed).toBeTrue();
  }));

  it('should pass card1Orientation to testHostReplug and update orientation', fakeAsync(() => {
    component.card1Orientation = 'CC1 (895mV / 58mV)';
    mockMauiService.testHostReplug.and.returnValue(
      of({
        allPassed: true,
        hostFlipOk: true,
        hostOrientation: 'CC2 (61mV / 938mV)',
        message: 'Host 180° Flip: PASS (CC1 -> CC2)',
      }),
    );
    component.runHostReplug();
    tick(3000);
    expect(mockMauiService.testHostReplug).toHaveBeenCalledWith(
      component.serialNumber,
      component.devicePort,
      'CC1 (895mV / 58mV)',
    );
    expect(component.replugPassed).toBeTrue();
    expect(component.card2Orientation).toBe('CC2 (61mV / 938mV)');
    expect(component.hostOrientation).toBe('CC2 (61mV / 938mV)');
  }));
});
