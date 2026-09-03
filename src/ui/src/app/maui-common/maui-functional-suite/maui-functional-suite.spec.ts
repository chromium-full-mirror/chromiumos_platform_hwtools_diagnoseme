// Copyright 2026 The ChromiumOS Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

import {
  ComponentFixture,
  TestBed,
  fakeAsync,
  tick,
} from '@angular/core/testing';
import {NoopAnimationsModule} from '@angular/platform-browser/animations';
import {provideRouter} from '@angular/router';
import {of} from 'rxjs';
import {MauiFunctionalSuiteComponent} from './maui-functional-suite';
import {MauiStation2Service} from '../../services/maui-station2.service';
import {MauiDeviceMgmtService} from '../../services/maui-device-mgmt.service';
import {MauiDialogService} from '../maui-dialog.service';

describe('MauiFunctionalSuiteComponent', () => {
  let component: MauiFunctionalSuiteComponent;
  let fixture: ComponentFixture<MauiFunctionalSuiteComponent>;

  const mockMauiService = {
    runHostOnlyTests: jasmine.createSpy().and.returnValue(
      of({
        allPassed: true,
        message: 'Host tests OK',
        mcuVersion: 'v1.0.0',
        pdcAppConfigVersion: '00000318',
        hostOrientation: 'CC1 (895mV / 58mV)',
        detectedHwrev: 2,
        hwrevOk: true,
        temperatureC: 35.5,
        tempOk: true,
        gpiosOk: true,
        gpioDetails: '21/21 Verified',
        voltagesOk: true,
        pp5000Mv: 5020,
        hostVbusMv: 5010,
        chgVbusMv: 19800,
        voltageDetails: 'Voltages within range',
      }),
    ),
    testHostReplug: jasmine.createSpy().and.returnValue(
      of({
        allPassed: true,
        message: 'Replug OK',
        hostOrientation: 'CC2 (60mV / 890mV)',
        detectedHwrev: 2,
        hwrevOk: true,
        temperatureC: 36.0,
        tempOk: true,
        gpiosOk: true,
        gpioDetails: '21/21 Verified',
        voltagesOk: true,
        pp5000Mv: 5020,
        hostVbusMv: 5010,
        chgVbusMv: 19800,
        voltageDetails: 'Voltages within range',
      }),
    ),
    captureResetBaseline: jasmine.createSpy().and.returnValue(
      of({
        success: true,
        initialUptimeMs: 12000,
        message: 'Baseline captured',
      }),
    ),
    testResetButton: jasmine.createSpy().and.returnValue(
      of({
        rebootOccurred: true,
        currentUptimeMs: 500,
        message: 'Reboot verified',
      }),
    ),
    readAdcBslStatus: jasmine.createSpy().and.returnValue(
      of({
        buttonPressed: true,
        bslMv: 3200,
        message: 'BSL button pressed',
      }),
    ),
    runHostDutTests: jasmine.createSpy().and.returnValue(
      of({
        allPassed: true,
        message: 'Host+DUT OK',
        dutOrientation: 'SBU1 (Normal)',
        h2hLinkSpeed: '5Gbps SuperSpeed',
        h2hSerialMatched: true,
        adbConnected: true,
        adbDutSpeed: '5Gbps',
        apUartOk: true,
        ecUartOk: true,
        gscUartOk: true,
        apVersionStr: 'v1',
        ecVersionStr: 'v1',
        gscVersionStr: 'v1',
        negotiatedVoltage: 20,
        negotiatedCurrent: 5,
        measuredVbusMv: 19800,
        calculatedPowerW: 100,
      }),
    ),
    test180Flip: jasmine.createSpy().and.returnValue(
      of({
        allPassed: true,
        message: 'Flip OK',
        dutOrientation: 'SBU2 (Flipped)',
      }),
    ),
    testChargerDisconnect: jasmine.createSpy().and.returnValue(
      of({
        allPassed: true,
        chargerDisconnected: true,
        message: 'Charger disconnected',
      }),
    ),
    writeDeviceLog: jasmine.createSpy().and.returnValue(
      of({
        success: true,
        message: 'Log written',
      }),
    ),
  };

  const mockDeviceMgmtService = {
    readConnectedDeviceSerial: jasmine.createSpy().and.returnValue(
      of({
        isConnected: true,
        serialNumber: 'MAUI-TEST-001',
        devicePort: '/dev/ttyUSB0',
        ftdiPid: 0x6002,
        adbSerial: 'MAUI-TEST-001',
        ftdiSerial: 'MAUI-TEST-001',
        dualEndpointsMatch: true,
        ftdiIsProgrammed: true,
      }),
    ),
    verifyScannedLabel: jasmine.createSpy().and.returnValue(
      of({
        matches: true,
        message: 'Outer barcode matches device serial MAUI-TEST-001',
        adbMatches: true,
        ftdiMatches: true,
        dualEndpointsMatch: true,
      }),
    ),
    saveCardLog: jasmine.createSpy().and.returnValue(
      of({
        success: true,
        savedFilePath:
          '/tmp/MAUI-TEST-001_diagnoseme_logs/smt_functional_summary.log',
        message: 'Log saved',
      }),
    ),
    getDeviceLogsStatus: jasmine.createSpy().and.returnValue(
      of({
        directoryExists: true,
        directoryPath: '/tmp/MAUI-TEST-001_diagnoseme_logs',
        logs: [],
        allRequiredLogsPresent: true,
        overallVerdict: 'PASS',
        message: '5 stage logs present',
      }),
    ),
    submitFinalManufacturingResults: jasmine.createSpy().and.returnValue(
      of({
        success: true,
        summaryFilePath:
          '/tmp/MAUI-TEST-001_diagnoseme_logs/final_results.json',
        mesTrackingId: 'MES-MAUI-TEST-001_2026-08-31_SMT_PASS',
        message: 'Saved',
      }),
    ),
  };

  const mockDialogService = {
    openFailure: jasmine.createSpy(),
  };

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [MauiFunctionalSuiteComponent, NoopAnimationsModule],
      providers: [
        provideRouter([]),
        {provide: MauiStation2Service, useValue: mockMauiService},
        {provide: MauiDeviceMgmtService, useValue: mockDeviceMgmtService},
        {provide: MauiDialogService, useValue: mockDialogService},
      ],
    }).compileComponents();

    fixture = TestBed.createComponent(MauiFunctionalSuiteComponent);
    component = fixture.componentInstance;
    fixture.detectChanges();
  });

  it('should create in SMT mode by default', () => {
    expect(component).toBeTruthy();
    expect(component.mode).toBe('smt');
    expect(component.isFatpMode).toBeFalse();
  });

  it('should support FATP QT0 mode with barcode verification', () => {
    component.mode = 'fatp_qt0';
    expect(component.isFatpMode).toBeTrue();
  });

  it('should verify barcode in FATP mode', () => {
    component.mode = 'fatp_qt0';
    component.scannedBarcode = 'MAUI-TEST-001';
    component.verifyBarcode();

    expect(mockDeviceMgmtService.verifyScannedLabel).toHaveBeenCalled();
    expect(component.barcodeMatches).toBeTrue();
  });

  it('should run host USB check and record orientation', fakeAsync(() => {
    component.runUsbCheck();
    tick(3000);
    expect(mockMauiService.runHostOnlyTests).toHaveBeenCalled();
    expect(component.usbCheckPassed).toBeTrue();
    expect(component.card1Orientation).toBe('CC1 (895mV / 58mV)');
  }));

  it('should run host replug verification', fakeAsync(() => {
    component.card1Orientation = 'CC1 (895mV / 58mV)';
    component.runHostReplug();
    tick(3000);
    expect(mockMauiService.testHostReplug).toHaveBeenCalled();
    expect(component.replugPassed).toBeTrue();
    expect(component.card2Orientation).toBe('CC2 (60mV / 890mV)');
  }));

  it('should execute full test sequence and save logs', fakeAsync(() => {
    component.usbCheckPassed = true;
    component.replugPassed = true;
    component.resetPassed = true;
    component.bslPassed = true;
    component.ledAmberChecked = true;
    component.ledGreenChecked = true;
    component.confirmLeds();
    expect(component.ledPassed).toBeTrue();

    component.runStage1();
    tick(3000);
    expect(component.stage1Passed).toBeTrue();

    component.runStage2();
    tick(3000);
    expect(component.stage2Passed).toBeTrue();

    component.runStage3();
    tick(3000);
    expect(component.stage3Passed).toBeTrue();

    component.saveLogs();
    expect(mockDeviceMgmtService.saveCardLog).toHaveBeenCalled();
    expect(
      mockDeviceMgmtService.submitFinalManufacturingResults,
    ).toHaveBeenCalled();
    expect(component.logsSaved).toBeTrue();
  }));
});
