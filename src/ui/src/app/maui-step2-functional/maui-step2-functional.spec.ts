// Copyright 2026 The ChromiumOS Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

import {ComponentFixture, TestBed} from '@angular/core/testing';
import {NoopAnimationsModule} from '@angular/platform-browser/animations';
import {provideRouter} from '@angular/router';
import {of} from 'rxjs';
import {MauiStep2FunctionalComponent} from './maui-step2-functional';
import {MauiStation2Service} from '../services/maui-station2.service';
import {MauiDeviceMgmtService} from '../services/maui-device-mgmt.service';
import {MauiDialogService} from '../maui-common/maui-dialog.service';

describe('MauiStep2FunctionalComponent', () => {
  let component: MauiStep2FunctionalComponent;
  let fixture: ComponentFixture<MauiStep2FunctionalComponent>;

  const mockMauiService = {
    runHostOnlyTests: jasmine
      .createSpy()
      .and.returnValue(of({allPassed: true})),
    testHostReplug: jasmine.createSpy().and.returnValue(of({allPassed: true})),
    testResetButton: jasmine.createSpy().and.returnValue(of({success: true})),
    testBslButton: jasmine.createSpy().and.returnValue(of({success: true})),
    runHostDutTests: jasmine.createSpy().and.returnValue(of({allPassed: true})),
  };

  const mockDeviceMgmtService = {
    readConnectedDeviceSerial: jasmine.createSpy().and.returnValue(
      of({
        isConnected: true,
        serialNumber: 'MAUI-TEST-001',
      }),
    ),
    saveCardLog: jasmine.createSpy().and.returnValue(of({success: true})),
    submitFinalManufacturingResults: jasmine
      .createSpy()
      .and.returnValue(of({success: true})),
  };

  const mockDialogService = {
    openFailure: jasmine.createSpy(),
  };

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [MauiStep2FunctionalComponent, NoopAnimationsModule],
      providers: [
        provideRouter([]),
        {provide: MauiStation2Service, useValue: mockMauiService},
        {provide: MauiDeviceMgmtService, useValue: mockDeviceMgmtService},
        {provide: MauiDialogService, useValue: mockDialogService},
      ],
    }).compileComponents();

    fixture = TestBed.createComponent(MauiStep2FunctionalComponent);
    component = fixture.componentInstance;
    fixture.detectChanges();
  });

  it('should create', () => {
    expect(component).toBeTruthy();
  });
});
