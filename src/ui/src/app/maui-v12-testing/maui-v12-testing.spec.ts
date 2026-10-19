// Copyright 2026 The ChromiumOS Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

import {ComponentFixture, TestBed} from '@angular/core/testing';
import {NoopAnimationsModule} from '@angular/platform-browser/animations';
import {provideRouter} from '@angular/router';
import {of} from 'rxjs';
import {MauiV12TestingComponent} from './maui-v12-testing';
import {MauiFunctionalSuiteComponent} from '../maui-common/maui-functional-suite/maui-functional-suite';
import {MauiStation2Service} from '../services/maui-station2.service';
import {MauiDeviceMgmtService} from '../services/maui-device-mgmt.service';
import {MauiMcuService} from '../services/maui-mcu.service';
import {MauiDialogService} from '../maui-common/maui-dialog.service';

describe('MauiV12TestingComponent', () => {
  let fixture: ComponentFixture<MauiV12TestingComponent>;

  const mockStation2 = {
    runHostOnlyTests: jasmine
      .createSpy()
      .and.returnValue(of({allPassed: true})),
    testHostReplug: jasmine.createSpy().and.returnValue(of({allPassed: true})),
    testResetButton: jasmine.createSpy().and.returnValue(of({success: true})),
    testBslButton: jasmine.createSpy().and.returnValue(of({success: true})),
    runHostDutTests: jasmine.createSpy().and.returnValue(of({allPassed: true})),
  };
  const mockDeviceMgmt = {
    readConnectedDeviceSerial: jasmine
      .createSpy()
      .and.returnValue(
        of({isConnected: true, serialNumber: 'MAUIV12H26010001'}),
      ),
    verifyScannedLabel: jasmine
      .createSpy()
      .and.returnValue(of({matches: true, message: 'Matched'})),
    saveCardLog: jasmine.createSpy().and.returnValue(of({success: true})),
    submitFinalManufacturingResults: jasmine
      .createSpy()
      .and.returnValue(of({success: true})),
  };
  const mockMcu = {
    provisionMcuSerial: jasmine
      .createSpy()
      .and.returnValue(
        of({success: true, mcuSerial: 'MAUIV12H26010001', message: 'ok'}),
      ),
  };

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [MauiV12TestingComponent, NoopAnimationsModule],
      providers: [
        provideRouter([]),
        {provide: MauiStation2Service, useValue: mockStation2},
        {provide: MauiDeviceMgmtService, useValue: mockDeviceMgmt},
        {provide: MauiMcuService, useValue: mockMcu},
        {
          provide: MauiDialogService,
          useValue: {openFailure: jasmine.createSpy()},
        },
      ],
    }).compileComponents();

    fixture = TestBed.createComponent(MauiV12TestingComponent);
    fixture.detectChanges();
  });

  function suite(): MauiFunctionalSuiteComponent {
    return fixture.debugElement.query(
      d => d.componentInstance instanceof MauiFunctionalSuiteComponent,
    ).componentInstance;
  }

  it('runs the shared suite as Maui v1.2', () => {
    expect(suite().hwRev).toBe('v1.2');
    expect(suite().isFatpMode).toBe(false);
    expect(suite().rev.consoleLabel).toBe('MCU');
    expect(suite().bslReadLabel).toBe('Start BSL Check');
  });

  it('gates the tests on serial number provisioning', () => {
    const el: HTMLElement = fixture.nativeElement;
    expect(el.querySelector('#func-card-serial')).toBeTruthy();
    expect(suite().serialStepDone).toBeFalse();
    suite().onSerialProvisionStatus({
      status: 'PASSED',
      serial: 'MAUIV12H26010001',
    });
    expect(suite().serialStepDone).toBeTrue();
    expect(suite().serialNumber).toBe('MAUIV12H26010001');
    expect(el.querySelector('#func-card-1')).toBeTruthy();
  });

  it('gates the tests on the H2H SPI flash access check', () => {
    const el: HTMLElement = fixture.nativeElement;
    expect(el.querySelector('#func-card-h2h-spi')).toBeTruthy();
    suite().onSerialProvisionStatus({
      status: 'PASSED',
      serial: 'MAUIV12H26010001',
    });
    expect(suite().serialCardsDone).toBeFalse();
    suite().onH2hSpiStatus({status: 'PASSED', serial: 'MAUIV12H26010001'});
    expect(suite().serialCardsDone).toBeTrue();
    fixture.detectChanges();
    expect(el.querySelector('#func-card-1 .disabled-card')).toBeNull();
  });
});
