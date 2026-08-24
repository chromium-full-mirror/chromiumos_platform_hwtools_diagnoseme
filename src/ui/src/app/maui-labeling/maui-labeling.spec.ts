// Copyright 2026 The ChromiumOS Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

import {ComponentFixture, TestBed} from '@angular/core/testing';
import {MauiLabelingComponent} from './maui-labeling';
import {MauiDeviceMgmtService} from '../services/maui-device-mgmt.service';
import {of} from 'rxjs';
import {NoopAnimationsModule} from '@angular/platform-browser/animations';
import {provideRouter} from '@angular/router';

describe('MauiLabelingComponent', () => {
  let component: MauiLabelingComponent;
  let fixture: ComponentFixture<MauiLabelingComponent>;

  const mockDeviceMgmtService = {
    readConnectedDeviceSerial: jasmine.createSpy().and.returnValue(
      of({
        isConnected: true,
        serialNumber: 'TEST_MAUI_SERIAL_001',
        devicePort: '/dev/ttyUSB0',
        productName: 'FTDI UART',
        message: 'Discovered',
        serialsMatched: true,
      }),
    ),
    getDeviceLogsStatus: jasmine.createSpy().and.returnValue(
      of({
        directoryExists: true,
        directoryPath: '/tmp/TEST_MAUI_SERIAL_001_diagnoseme_logs',
        logs: [],
        allRequiredLogsPresent: true,
        message: 'All logs present',
      }),
    ),
    verifyScannedLabel: jasmine.createSpy().and.returnValue(
      of({
        matches: true,
        message: 'Label matches electronic serial.',
      }),
    ),
    submitFinalManufacturingResults: jasmine.createSpy().and.returnValue(
      of({
        success: true,
        mesTrackingId: 'MES-TEST-123',
        summaryFilePath:
          '/tmp/TEST_MAUI_SERIAL_001_diagnoseme_logs/final_results.json',
        message: 'Saved',
      }),
    ),
  };

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [MauiLabelingComponent, NoopAnimationsModule],
      providers: [
        provideRouter([]),
        {provide: MauiDeviceMgmtService, useValue: mockDeviceMgmtService},
      ],
    }).compileComponents();

    fixture = TestBed.createComponent(MauiLabelingComponent);
    component = fixture.componentInstance;
    fixture.detectChanges();
  });

  it('should create', () => {
    expect(component).toBeTruthy();
  });
});
