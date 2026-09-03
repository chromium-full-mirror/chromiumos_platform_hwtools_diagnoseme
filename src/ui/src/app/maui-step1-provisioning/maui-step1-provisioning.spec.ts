// Copyright 2026 The ChromiumOS Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

import {ComponentFixture, TestBed} from '@angular/core/testing';
import {NoopAnimationsModule} from '@angular/platform-browser/animations';
import {provideRouter} from '@angular/router';
import {of} from 'rxjs';
import {MauiStep1ProvisioningComponent} from './maui-step1-provisioning';
import {MauiStation1Service} from '../services/maui-station1.service';
import {MauiPdcService} from '../services/maui-pdc.service';
import {MauiDeviceMgmtService} from '../services/maui-device-mgmt.service';
import {MauiDialogService} from '../maui-common/maui-dialog.service';

describe('MauiStep1ProvisioningComponent', () => {
  let component: MauiStep1ProvisioningComponent;
  let fixture: ComponentFixture<MauiStep1ProvisioningComponent>;

  const mockStation1Service = {
    streamBslAutoDiscovery: jasmine.createSpy().and.returnValue(
      of({
        bslDetected: true,
        devicePort: '/dev/ttyUSB0',
        pid: 0x6001,
        autoFlashEligible: true,
        message: 'BSL device found',
      }),
    ),
    autoProgramMcuBsl: jasmine.createSpy().and.returnValue(
      of({
        success: true,
        message: 'MCU programmed',
      }),
    ),
    discoverTopologyAndSerial: jasmine.createSpy().and.returnValue(
      of({
        success: true,
        readSerialNumber: 'MAUI-TEST-001',
        ftdiCurrentSerial: 'FT123456',
        ftdiCurrentPid: 0x6001,
      }),
    ),
    programFtdiEeprom: jasmine.createSpy().and.returnValue(
      of({
        success: true,
        message: 'FTDI programmed',
      }),
    ),
  };

  const mockPdcService = {
    discoverConnectedPdcDevices: jasmine.createSpy().and.returnValue(
      of({
        devices: [],
      }),
    ),
    programSinglePdc: jasmine.createSpy().and.returnValue(
      of({
        isComplete: true,
        progressPercent: 100,
        currentBlock: 'Block 4/4',
        statusMessage: 'PDC Flash Complete',
      }),
    ),
  };

  const mockDeviceMgmtService = {
    saveCardLog: jasmine.createSpy().and.returnValue(
      of({
        success: true,
        message: 'Saved',
      }),
    ),
    submitFinalManufacturingResults: jasmine
      .createSpy()
      .and.returnValue(of({success: true})),
  };

  const mockDialogService = {
    openFailure: jasmine.createSpy(),
  };

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [MauiStep1ProvisioningComponent, NoopAnimationsModule],
      providers: [
        provideRouter([]),
        {provide: MauiStation1Service, useValue: mockStation1Service},
        {provide: MauiPdcService, useValue: mockPdcService},
        {provide: MauiDeviceMgmtService, useValue: mockDeviceMgmtService},
        {provide: MauiDialogService, useValue: mockDialogService},
      ],
    }).compileComponents();

    fixture = TestBed.createComponent(MauiStep1ProvisioningComponent);
    component = fixture.componentInstance;
    fixture.detectChanges();
  });

  it('should create', () => {
    expect(component).toBeTruthy();
  });

  it('should auto-run pipeline (MCU -> PDC -> FTDI) on slot', () => {
    const slot = component.slots[0];
    slot.devicePort = '/dev/ttyUSB0';
    component.startPipelineForSlot(slot);

    expect(mockStation1Service.autoProgramMcuBsl).toHaveBeenCalled();
    expect(mockPdcService.programSinglePdc).toHaveBeenCalled();
    expect(mockStation1Service.programFtdiEeprom).toHaveBeenCalled();
    expect(slot.stage).toBe('PROVISIONED');
    expect(component.pipelineCardPassed).toBeTrue();
  });

  it('should dynamically allocate separate slots for multiple connected devices', () => {
    // Slot 1 bound on init
    expect(component.slots[0].isDetected).toBeTrue();
    expect(component.slots[0].devicePort).toBe('/dev/ttyUSB0');

    // Simulate second device arriving on /dev/ttyUSB1
    const mockRes2 = {
      bslDetected: true,
      devicePort: '/dev/ttyUSB1',
      pid: 0x6001,
      autoFlashEligible: true,
      message: 'Second device',
    };
    mockStation1Service.streamBslAutoDiscovery.and.returnValue(of(mockRes2));
    component.startBslDiscoveryStream();

    expect(component.slots[1].isDetected).toBeTrue();
    expect(component.slots[1].devicePort).toBe('/dev/ttyUSB1');
  });

  it('should verify disconnect before saving batch logs', () => {
    const slot = component.slots[0];
    slot.stage = 'PROVISIONED';
    component.pipelineCardPassed = true;
    component.connectedDevicesCount = 0;
    component.allDisconnected = true;
    component.saveBatchLogsAndFinalize();

    expect(mockDeviceMgmtService.saveCardLog).toHaveBeenCalled();
    expect(component.logsSaved).toBeTrue();
  });

  it('should allow saving logs when partial slots (e.g. 2/4) have completed', () => {
    component.slots[0].stage = 'PROVISIONED';
    component.slots[0].serialNumber = 'MAUI-001';
    component.slots[1].stage = 'PROVISIONED';
    component.slots[1].serialNumber = 'MAUI-002';
    component.slots[2].stage = 'IDLE';
    component.slots[3].stage = 'IDLE';

    expect(component.hasCompletedSlots).toBeTrue();
    expect(component.canSaveLogs).toBeTrue();

    component.connectedDevicesCount = 0;
    component.saveBatchLogsAndFinalize();
    expect(mockDeviceMgmtService.saveCardLog).toHaveBeenCalled();
  });
});
