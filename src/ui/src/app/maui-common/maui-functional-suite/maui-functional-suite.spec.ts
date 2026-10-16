// Copyright 2026 The ChromiumOS Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

import {
  ComponentFixture,
  TestBed,
  discardPeriodicTasks,
  fakeAsync,
  tick,
} from '@angular/core/testing';
import {By} from '@angular/platform-browser';
import {NoopAnimationsModule} from '@angular/platform-browser/animations';
import {provideRouter} from '@angular/router';
import {Subject, of, throwError} from 'rxjs';
import {MauiFunctionalSuiteComponent} from './maui-functional-suite';
import {MauiH2hSpiCheckCardComponent} from '../maui-h2h-spi-check-card/maui-h2h-spi-check-card';
import {MauiSerialProvisionCardComponent} from '../maui-serial-provision-card/maui-serial-provision-card';
import {MauiStation2Service} from '../../services/maui-station2.service';
import {MauiDeviceMgmtService} from '../../services/maui-device-mgmt.service';
import {MauiMcuService} from '../../services/maui-mcu.service';
import {MauiDialogService} from '../maui-dialog.service';

describe('MauiFunctionalSuiteComponent', () => {
  let component: MauiFunctionalSuiteComponent;
  let fixture: ComponentFixture<MauiFunctionalSuiteComponent>;

  const connectedDevice = {
    isConnected: true,
    serialNumber: 'MAUI-TEST-001',
    devicePort: '/dev/ttyUSB0',
    ftdiPid: 0x6002,
    adbSerial: 'MAUI-TEST-001',
    ftdiSerial: 'MAUI-TEST-001',
    dualEndpointsMatch: true,
    ftdiIsProgrammed: true,
  };

  const adbTransferPassed = {
    success: true,
    message: 'ADB transfer OK',
    adbSerial: 'DUT123',
    fileSizeBytes: 400000000,
    pushSeconds: 8,
    pullSeconds: 8,
    pushMbPerS: 50,
    pullMbPerS: 50,
    totalSeconds: 18,
  };

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
    runAdbTransferTest: jasmine
      .createSpy()
      .and.returnValue(of(adbTransferPassed)),
    writeDeviceLog: jasmine.createSpy().and.returnValue(
      of({
        success: true,
        message: 'Log written',
      }),
    ),
  };

  const mockDeviceMgmtService = {
    readConnectedDeviceSerial: jasmine
      .createSpy()
      .and.returnValue(of(connectedDevice)),
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
    // The mocks are shared by all specs and jasmine runs them in random
    // order: undo what a spec changed (e.g. a failing ADB transfer).
    mockMauiService.runAdbTransferTest.and.returnValue(of(adbTransferPassed));
    mockDeviceMgmtService.readConnectedDeviceSerial.and.returnValue(
      of(connectedDevice),
    );
    mockDeviceMgmtService.saveCardLog.calls.reset();
    mockDialogService.openFailure.calls.reset();

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

  it('focuses the Card 1 button after the barcode matched', fakeAsync(() => {
    component.mode = 'fatp_qt0';
    fixture.detectChanges();
    const focus = spyOn(component.runUsbCheckBtn!.nativeElement, 'focus');
    component.scannedBarcode = 'MAUI-TEST-001';
    component.verifyBarcode();
    tick(500);
    expect(focus).toHaveBeenCalled();
  }));

  it('keeps polling the device after a failed poll', fakeAsync(() => {
    mockDeviceMgmtService.readConnectedDeviceSerial.and.returnValues(
      throwError(() => new Error('UNAVAILABLE')),
      of({...connectedDevice, isConnected: false}),
    );
    const polled = TestBed.createComponent(MauiFunctionalSuiteComponent);
    polled.detectChanges();
    tick(0);
    expect(polled.componentInstance.statusMessage).toBe(
      'Device detection error.',
    );
    tick(1500);
    expect(polled.componentInstance.deviceConnected).toBeFalse();
    expect(polled.componentInstance.statusMessage).toContain(
      'No Maui Device Detected',
    );
    polled.destroy();
  }));

  it('drops pending test results when the page is left', () => {
    const pending = new Subject<typeof adbTransferPassed>();
    mockMauiService.runAdbTransferTest.and.returnValue(pending);
    component.stage3Passed = true;
    component.runAdbTransfer();
    expect(pending.observed).toBeTrue();
    fixture.destroy();
    expect(pending.observed).toBeFalse();
  });

  it('does not reload once the page was left', fakeAsync(() => {
    const reload = spyOn(component, 'reloadForNextUnit');
    component.adbPassed = true;
    component.deviceConnected = false;
    component.saveLogs();
    fixture.destroy();
    tick(2500);
    expect(reload).not.toHaveBeenCalled();
  }));

  it('logs a bypassed ADB transfer as BYPASSED', () => {
    component.serialNumber = 'MAUI-TEST-001'; // As reported by the device poll.
    component.stage3Passed = true;
    component.bypassAdbTransfer();
    expect(component.adbStatusValue).toBe('BYPASSED');
    const [serial, step, content] =
      mockDeviceMgmtService.saveCardLog.calls.mostRecent().args;
    expect(serial).toBe('MAUI-TEST-001');
    expect(step).toBe('adb_transfer_test');
    expect(content).toContain('STATUS=BYPASSED');
    expect(content).toContain('Bypassed by operator.');
    expect(mockDeviceMgmtService.getDeviceLogsStatus).toHaveBeenCalled();
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
    const reload = spyOn(component, 'reloadForNextUnit');
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
    expect(component.canSaveLogs).toBeFalse();

    component.runAdbTransfer();
    expect(mockMauiService.runAdbTransferTest).toHaveBeenCalled();
    expect(component.adbPassed).toBeTrue();

    component.deviceConnected = false;
    component.saveLogs();
    expect(mockDeviceMgmtService.saveCardLog).toHaveBeenCalled();
    const summary = mockDeviceMgmtService.saveCardLog.calls.mostRecent()
      .args[2] as string;
    expect(summary).toContain(
      'Card 9 (ADB Transfer, two ~400 MB random files, must finish within 60 s): PASSED',
    );
    expect(summary).toContain('push 50.0 MB/s, pull 50.0 MB/s');
    expect(
      mockDeviceMgmtService.submitFinalManufacturingResults,
    ).toHaveBeenCalled();
    expect(component.logsSaved).toBeTrue();
    tick(2500);
    expect(reload).toHaveBeenCalled();
  }));

  it('offers retry and bypass when the ADB transfer fails', () => {
    mockMauiService.runAdbTransferTest.and.returnValue(
      of({success: false, message: 'Data corrupted on pull'}),
    );
    component.stage3Passed = true;
    component.runAdbTransfer();
    expect(component.adbPassed).toBeFalse();
    expect(component.adbStatusValue).toBe('FAILED');
    expect(mockDialogService.openFailure).toHaveBeenCalledWith(
      'ADB Transfer Test Failed',
      'Data corrupted on pull',
      jasmine.any(Function),
      jasmine.any(Function),
    );
    const bypass = mockDialogService.openFailure.calls.mostRecent().args[3];
    bypass();
    expect(component.adbPassed).toBeTrue();
    expect(component.adbStatusValue).toBe('BYPASSED');
  });
});

describe('MauiFunctionalSuiteComponent hardware revisions', () => {
  let component: MauiFunctionalSuiteComponent;
  let fixture: ComponentFixture<MauiFunctionalSuiteComponent>;
  let station2: jasmine.SpyObj<MauiStation2Service>;
  let dialog: jasmine.SpyObj<MauiDialogService>;
  let deviceMgmt: jasmine.SpyObj<MauiDeviceMgmtService>;
  let mcu: jasmine.SpyObj<MauiMcuService>;

  const SN = 'MAUIV12H26090104';
  const hostOnly = {
    allPassed: true,
    summary: 'ok',
    hostOrientation: 'CC1 (418mV / 5mV)',
    detectedHwrev: 3,
    hwrevOk: true,
    temperatureC: 35,
    tempOk: true,
    gpiosOk: true,
    gpioDetails:
      'All 29 GPIOs verified (incl. usb3_mux_host_sel_r for the host CC line)',
    voltagesOk: true,
    pp5000Mv: 2516,
    hostVbusMv: 2519,
    chgVbusMv: 5021,
  };

  function create(hwRev: 'v1.1' | 'v1.2'): void {
    fixture = TestBed.createComponent(MauiFunctionalSuiteComponent);
    component = fixture.componentInstance;
    component.hwRev = hwRev;
    fixture.detectChanges();
  }

  function text(selector: string): string {
    return (
      (fixture.nativeElement as HTMLElement).querySelector(selector)
        ?.textContent ?? ''
    );
  }

  beforeEach(async () => {
    station2 = jasmine.createSpyObj('MauiStation2Service', [
      'runHostOnlyTests',
      'testHostReplug',
      'testBslButton',
    ]);
    dialog = jasmine.createSpyObj('MauiDialogService', ['openFailure']);
    deviceMgmt = jasmine.createSpyObj('MauiDeviceMgmtService', [
      'readConnectedDeviceSerial',
      'saveCardLog',
      'submitFinalManufacturingResults',
      'getDeviceLogsStatus',
    ]);
    deviceMgmt.readConnectedDeviceSerial.and.returnValue(
      of({
        isConnected: true,
        serialNumber: 'MAUIV12H26090104',
        devicePort: '/dev/ttyACM0',
      }) as never,
    );
    deviceMgmt.saveCardLog.and.returnValue(of({success: true}) as never);
    deviceMgmt.submitFinalManufacturingResults.and.returnValue(
      of({success: true}) as never,
    );
    deviceMgmt.getDeviceLogsStatus.and.returnValue(of({logs: []}) as never);
    mcu = jasmine.createSpyObj('MauiMcuService', [
      'provisionMcuSerial',
      'checkH2hSpiSerial',
    ]);
    await TestBed.configureTestingModule({
      imports: [MauiFunctionalSuiteComponent, NoopAnimationsModule],
      providers: [
        provideRouter([]),
        {provide: MauiStation2Service, useValue: station2},
        {provide: MauiDeviceMgmtService, useValue: deviceMgmt},
        {provide: MauiMcuService, useValue: mcu},
        {provide: MauiDialogService, useValue: dialog},
      ],
    }).compileComponents();
  });

  it('shows the expected hwrev reported by the server', fakeAsync(() => {
    create('v1.2');
    station2.runHostOnlyTests.and.returnValue(
      of({...hostOnly, expectedHwrev: '3 or 4'}) as never,
    );
    component.runUsbCheck();
    tick(3000);
    fixture.detectChanges();
    expect(component.card1ExpectedHwrev).toBe('3 or 4');
    expect(text('#func-card-1')).toContain('3 (Expected: 3 or 4)');
    expect(text('#func-card-1')).toContain('All 29 GPIOs verified');
    discardPeriodicTasks();
  }));

  it('falls back to the profile when the server sends no expected hwrev', fakeAsync(() => {
    create('v1.1');
    station2.runHostOnlyTests.and.returnValue(
      of({...hostOnly, detectedHwrev: 2}) as never,
    );
    component.runUsbCheck();
    tick(3000);
    fixture.detectChanges();
    expect(component.card1ExpectedHwrev).toBe('2');
    expect(text('#func-card-1')).toContain('2 (Expected: 2)');
    expect(text('#func-card-1')).toContain('19/19 Verified');
    discardPeriodicTasks();
  }));

  it('uses the expected hwrev of the server on the flip card', fakeAsync(() => {
    create('v1.2');
    station2.testHostReplug.and.returnValue(
      of({...hostOnly, message: 'ok', expectedHwrev: '3'}) as never,
    );
    component.runHostReplug();
    tick(3000);
    expect(component.card2ExpectedHwrev).toBe('3');
    discardPeriodicTasks();
  }));

  it('shows the v1.2 LED texts', () => {
    create('v1.2');
    const led = text('#func-card-5');
    expect(led).toContain('Green LED (MCU App)');
    expect(led).toContain('BLINKING at about 1 Hz');
    expect(led).toContain('Amber LED (Charger)');
    expect(led).toContain('SOLID ON while the charger has a PD contract');
    expect(led).not.toContain('Host Link');
    component.confirmLeds();
    expect(dialog.openFailure.calls.mostRecent().args[1]).toContain(
      'blinks at about 1 Hz',
    );
  });

  it('keeps the v1.1 LED texts', () => {
    create('v1.1');
    const led = text('#func-card-5');
    expect(led).toContain('Amber LED (PD Contract)');
    expect(led).toContain('Must be ON when charger plugged.');
    expect(led).toContain('Green LED (Host Link)');
    expect(led).toContain('Must be BLINKING at 1 Hz (500ms ON / 500ms OFF).');
    component.confirmLeds();
    expect(dialog.openFailure.calls.mostRecent().args[1]).toBe(
      'Please visually inspect and confirm Amber LED is ON and Green LED is BLINKING at 1 Hz.',
    );
  });

  function cardIds(): string[] {
    const el: HTMLElement = fixture.nativeElement;
    return Array.from(el.querySelectorAll('.cards-column [id^="func-card"]'))
      .map(e => e.id)
      .filter(id => id);
  }

  function card1Disabled(): boolean {
    fixture.detectChanges();
    return !!(fixture.nativeElement as HTMLElement).querySelector(
      '#func-card-1 .disabled-card',
    );
  }

  function child<T>(type: new (...args: never[]) => T): T {
    return fixture.debugElement.query(By.directive(type)).componentInstance;
  }

  it('shows the H2H SPI card right after the serial card on v1.2', () => {
    create('v1.2');
    const ids = cardIds();
    expect(ids.slice(0, 3)).toEqual([
      'func-card-serial',
      'func-card-h2h-spi',
      'func-card-1',
    ]);
    expect(text('#func-card-h2h-spi')).toContain(
      '2. H2H Flash Access Check (SPI)',
    );
    expect(text('#func-card-serial')).toContain(
      '1. Serial Number Provisioning',
    );
  });

  it('numbers the v1.2 cards after the serial and H2H SPI cards', () => {
    create('v1.2');
    expect(text('#func-card-1')).toContain(
      '3. Host USB & Console Verification',
    );
    expect(text('#func-card-adb')).toContain(
      '11. ADB Data Transfer Reliability',
    );
    expect(text('#func-card-9')).toContain('12. SMT Log Completion & Archival');
    expect(component.bslCardTitle).toBe(
      '6. Interactive BSL Button Test (GPIO)',
    );
  });

  it('keeps the v1.1 card numbers', () => {
    create('v1.1');
    expect(text('#func-card-1')).toContain(
      '1. Host USB & Console Verification',
    );
    expect(text('#func-card-adb')).toContain(
      '9. ADB Data Transfer Reliability',
    );
    expect(text('#func-card-9')).toContain('10. SMT Log Completion & Archival');
    expect(component.bslCardTitle).toBe(
      '4. Interactive BSL Button Test (ADC Status)',
    );
  });

  it('has no serial or H2H SPI card on v1.1', () => {
    create('v1.1');
    expect(cardIds()).not.toContain('func-card-serial');
    expect(cardIds()).not.toContain('func-card-h2h-spi');
    expect(component.serialCardsDone).toBeTrue();
    expect(card1Disabled()).toBeFalse();
  });

  it('enables Card 1 only when the serial and H2H steps are done', () => {
    create('v1.2');
    expect(card1Disabled()).toBeTrue();
    component.onSerialProvisionStatus({status: 'PASSED', serial: SN});
    expect(card1Disabled()).toBeTrue();
    component.onH2hSpiStatus({status: 'FAILED', serial: ''});
    expect(card1Disabled()).toBeTrue();
    component.onH2hSpiStatus({status: 'BYPASSED', serial: ''});
    expect(card1Disabled()).toBeFalse();
    component.onSerialProvisionStatus({status: 'FAILED', serial: ''});
    expect(card1Disabled()).toBeTrue();
    component.onSerialProvisionStatus({status: 'BYPASSED', serial: ''});
    component.onH2hSpiStatus({status: 'PASSED', serial: SN});
    expect(card1Disabled()).toBeFalse();
  });

  it('locks the serial and H2H SPI cards while another test runs', () => {
    create('v1.2');
    const serialCard = child(MauiSerialProvisionCardComponent);
    const h2hCard = child(MauiH2hSpiCheckCardComponent);
    expect(serialCard.locked).toBeFalse();
    expect(h2hCard.locked).toBeFalse();

    component.h2hSpiInProgress = true;
    fixture.detectChanges();
    expect(serialCard.locked).toBeTrue();
    expect(h2hCard.locked).toBeFalse();

    component.h2hSpiInProgress = false;
    component.serialProvisionInProgress = true;
    fixture.detectChanges();
    expect(serialCard.locked).toBeFalse();
    expect(h2hCard.locked).toBeTrue();

    component.serialProvisionInProgress = false;
    component.usbCheckInProgress = true;
    fixture.detectChanges();
    expect(serialCard.locked).toBeTrue();
    expect(h2hCard.locked).toBeTrue();
  });

  it('runs the H2H SPI check for the detected board', () => {
    create('v1.2');
    // As reported by the device poll.
    component.serialNumber = SN;
    component.devicePort = '/dev/ttyACM0';
    fixture.detectChanges();
    mcu.checkH2hSpiSerial.and.returnValue(
      of({
        success: true,
        firmwareSupported: true,
        hostSerialBefore: SN,
        spiSerial: SN,
        hostSerialAfter: SN,
        message: 'ok',
      }) as never,
    );
    const running: boolean[] = [];
    child(MauiH2hSpiCheckCardComponent).runningChange.subscribe(r =>
      running.push(r),
    );
    const button: HTMLButtonElement = (
      fixture.nativeElement as HTMLElement
    ).querySelector('#func-card-h2h-spi button')!;
    button.click();
    expect(mcu.checkH2hSpiSerial).toHaveBeenCalledWith(SN, '/dev/ttyACM0');
    expect(running).toEqual([true, false]);
    expect(component.h2hSpiInProgress).toBeFalse();
    expect(component.h2hSpiStatus).toBe('PASSED');
    expect(component.h2hSpiSerial).toBe(SN);
    expect(component.h2hSpiStepDone).toBeTrue();
  });

  it('reports the v1.2 serial cards in the summary', () => {
    create('v1.2');
    spyOn(component, 'reloadForNextUnit');
    component.onSerialProvisionStatus({status: 'PASSED', serial: SN});
    component.onH2hSpiStatus({status: 'PASSED', serial: SN});
    component.adbPassed = true;
    component.deviceConnected = false;
    component.saveLogs();
    const summary = deviceMgmt.saveCardLog.calls.mostRecent().args[2];
    expect(summary).toContain('Card 1 (Serial Provisioning): PASSED');
    expect(summary).toContain(
      `Card 2 (H2H SPI Flash Access): PASSED (SPI serial ${SN})`,
    );
    expect(summary).toContain('Card 3 (Host USB & Console): ');
    expect(summary).toContain(
      'Card 6 (BSL Button GPIO): NOT_RUN (BSL GPIO: 0)',
    );
    expect(summary).toContain('Card 11 (ADB Transfer, two ~400 MB');
    expect(summary).not.toContain('Card 0');
  });

  it('keeps the v1.1 summary without serial cards', () => {
    create('v1.1');
    spyOn(component, 'reloadForNextUnit');
    component.adbPassed = true;
    component.deviceConnected = false;
    component.saveLogs();
    const summary = deviceMgmt.saveCardLog.calls.mostRecent().args[2];
    expect(summary).not.toContain('Card 0');
    expect(summary).not.toContain('Serial Provisioning');
    expect(summary).toContain('Card 1 (Host USB & Console): ');
    expect(summary).toContain(
      'Card 4 (BSL Button ADC): NOT_RUN (Peak BSL: 0 mV)',
    );
    expect(summary).toContain('Card 9 (ADB Transfer, two ~400 MB');
  });

  it('logs bypassed serial and H2H SPI cards as BYPASSED', () => {
    create('v1.2');
    component.serialNumber = SN;
    component.onSerialProvisionStatus({
      status: 'FAILED',
      serial: '',
      message: "H2H serial 'X' is not a valid Maui v1.2 serial.",
    });
    component.onSerialProvisionStatus({
      status: 'BYPASSED',
      serial: '',
      message: 'Serial number provisioning bypassed by operator.',
    });
    let [serial, step, content] =
      deviceMgmt.saveCardLog.calls.mostRecent().args;
    expect([serial, step]).toEqual([SN, 'mcu_serial_provisioning']);
    expect(content).toContain('STATUS=BYPASSED');
    expect(content).toContain(
      "Last result before the bypass: H2H serial 'X' is not a valid",
    );
    component.onH2hSpiStatus({status: 'BYPASSED', serial: '', message: ''});
    [serial, step, content] = deviceMgmt.saveCardLog.calls.mostRecent().args;
    expect([serial, step]).toEqual([SN, 'h2h_spi_check']);
    expect(content).toContain('Bypassed by operator.');
    expect(content).not.toContain('Last result');
  });

  it('shows a BSL GPIO reading of 0 on v1.2', () => {
    create('v1.2');
    station2.testBslButton.and.returnValue(
      of({
        success: false,
        peakBslMv: 0,
        thresholdMv: 1,
        message: 'BSL Button Test FAILED. GPIO ec_bsl_invoke did not read 1.',
      }) as never,
    );
    component.resetPassed = true;
    component.readBslButton();
    fixture.detectChanges();
    expect(text('#func-card-4')).toContain('BSL GPIO:');
    expect(text('#func-card-4')).toContain('0 (Expected 1)');
  });

  it('keeps hiding a 0 mV BSL reading on v1.1', () => {
    create('v1.1');
    station2.testBslButton.and.returnValue(
      of({success: false, peakBslMv: 0, message: 'BSL failed'}) as never,
    );
    component.resetPassed = true;
    component.readBslButton();
    fixture.detectChanges();
    expect(text('#func-card-4')).not.toContain('ADC Voltage:');
  });
});
