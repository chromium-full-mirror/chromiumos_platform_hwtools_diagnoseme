// Copyright 2026 The ChromiumOS Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

import {
  ComponentFixture,
  TestBed,
  fakeAsync,
  tick,
} from '@angular/core/testing';
import {MauiDutTestingComponent} from './maui-dut-testing';
import {MauiStation2Service} from '../services/maui-station2.service';
import {MauiDeviceMgmtService} from '../services/maui-device-mgmt.service';
import {of} from 'rxjs';
import {NoopAnimationsModule} from '@angular/platform-browser/animations';
import {provideRouter} from '@angular/router';

describe('MauiDutTestingComponent', () => {
  let component: MauiDutTestingComponent;
  let fixture: ComponentFixture<MauiDutTestingComponent>;

  const mockMauiService = {
    runHostDutTests: jasmine.createSpy().and.returnValue(
      of({
        allPassed: true,
        h2hSpeed5gbps: true,
        h2hSerialMatched: true,
        pdContractOk: true,
        negotiatedVoltageV: 20.04,
        negotiatedCurrentA: 4.95,
        apUartOk: true,
        ecUartOk: true,
        gscUartOk: true,
        sbuFlipOk: true,
        summary: 'All OK',
      }),
    ),
  };

  const mockDeviceMgmtService = {
    readConnectedDeviceSerial: jasmine.createSpy().and.returnValue(
      of({
        isConnected: true,
        serialNumber: 'TEST_MAUI_001',
      }),
    ),
    saveCardLog: jasmine
      .createSpy()
      .and.returnValue(of({success: true, message: 'Saved'})),
  };

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [MauiDutTestingComponent, NoopAnimationsModule],
      providers: [
        provideRouter([]),
        {provide: MauiStation2Service, useValue: mockMauiService},
        {provide: MauiDeviceMgmtService, useValue: mockDeviceMgmtService},
      ],
    }).compileComponents();

    fixture = TestBed.createComponent(MauiDutTestingComponent);
    component = fixture.componentInstance;
    fixture.detectChanges();
  });

  it('should create', () => {
    expect(component).toBeTruthy();
  });

  it('should run stage 1 with charger', fakeAsync(() => {
    component.serialNumber = 'TEST_MAUI_001';
    component.runStage1WithCharger();
    tick(3000);
    expect(mockMauiService.runHostDutTests).toHaveBeenCalledWith(
      'TEST_MAUI_001',
      'TEST_MAUI_001',
      'stage1_with_charger',
    );
    expect(component.stage1Passed).toBeTrue();
  }));

  it('should run stage 2 SBU flip', fakeAsync(() => {
    component.serialNumber = 'TEST_MAUI_001';
    component.runStage2SbuFlip();
    tick(3000);
    expect(mockMauiService.runHostDutTests).toHaveBeenCalledWith(
      'TEST_MAUI_001',
      'TEST_MAUI_001',
      'stage2_sbu_flip',
    );
    expect(component.stage2Passed).toBeTrue();
  }));

  it('should run stage 3 no charger', fakeAsync(() => {
    component.serialNumber = 'TEST_MAUI_001';
    component.runStage3NoCharger();
    tick(3000);
    expect(mockMauiService.runHostDutTests).toHaveBeenCalledWith(
      'TEST_MAUI_001',
      'TEST_MAUI_001',
      'stage3_no_charger',
    );
    expect(component.stage3Passed).toBeTrue();
  }));
});
