// Copyright 2026 The ChromiumOS Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

import {
  ComponentFixture,
  TestBed,
  discardPeriodicTasks,
  fakeAsync,
  flush,
  tick,
} from '@angular/core/testing';
import {NoopAnimationsModule} from '@angular/platform-browser/animations';
import {provideRouter} from '@angular/router';
import {Subject, of, throwError} from 'rxjs';
import {MauiFunctionalSuiteComponent} from './maui-functional-suite';
import {MauiStation2Service} from '../../services/maui-station2.service';
import {MauiDeviceMgmtService} from '../../services/maui-device-mgmt.service';
import {MauiDialogService} from '../maui-dialog.service';
import {PageReloadService} from '../page-reload.service';
import {
  DEVICE_POLL_INTERVAL_MS,
  PAGE_RELOAD_DELAY_MS,
  STABILIZATION_DELAY_MS,
} from '../maui-constants';
import {
  MauiAdbTransferTestResponse,
  MauiBslButtonTestResponse,
  MauiFinalSubmissionResponse,
  MauiHostDutTestResponse,
  MauiHostOnlyTestResponse,
  MauiHostReplugTestResponse,
  MauiLogsStatusResponse,
  MauiReadSerialResponse,
  MauiResetButtonTestResponse,
  MauiSaveLogResponse,
  MauiVerifyLabelResponse,
} from '../../../proto/maui-manufacturing.pb';

const SERIAL = 'MAUI-TEST-001';

function connected(serial = SERIAL): MauiReadSerialResponse {
  return new MauiReadSerialResponse({
    isConnected: true,
    serialNumber: serial,
    devicePort: '/dev/ttyUSB3',
    ftdiPid: 0x6002,
    adbSerial: serial,
    ftdiSerial: serial,
    dualEndpointsMatch: true,
    ftdiIsProgrammed: true,
  });
}

const DISCONNECTED = new MauiReadSerialResponse({isConnected: false});

function adbTransferPassed(): MauiAdbTransferTestResponse {
  return new MauiAdbTransferTestResponse({
    success: true,
    message: 'ADB transfer OK',
    adbSerial: 'DUT123',
    fileSizeBytes: '800000000',
    pushSeconds: 16,
    pullSeconds: 16,
    pushMbPerS: 50,
    pullMbPerS: 50,
    totalSeconds: 40,
  });
}

describe('MauiFunctionalSuiteComponent', () => {
  let fixture: ComponentFixture<MauiFunctionalSuiteComponent>;
  let component: MauiFunctionalSuiteComponent;
  let station2: jasmine.SpyObj<MauiStation2Service>;
  let deviceMgmt: jasmine.SpyObj<MauiDeviceMgmtService>;
  let dialog: jasmine.SpyObj<MauiDialogService>;
  let pageReload: jasmine.SpyObj<PageReloadService>;

  beforeEach(async () => {
    station2 = jasmine.createSpyObj<MauiStation2Service>(
      'MauiStation2Service',
      [
        'runHostOnlyTests',
        'testHostReplug',
        'testResetButton',
        'testBslButton',
        'runHostDutTests',
        'runAdbTransferTest',
      ],
    );
    deviceMgmt = jasmine.createSpyObj<MauiDeviceMgmtService>(
      'MauiDeviceMgmtService',
      [
        'readConnectedDeviceSerial',
        'verifyScannedLabel',
        'getDeviceLogsStatus',
        'saveCardLog',
        'submitFinalManufacturingResults',
      ],
    );
    dialog = jasmine.createSpyObj<MauiDialogService>('MauiDialogService', [
      'openFailure',
    ]);
    pageReload = jasmine.createSpyObj<PageReloadService>('PageReloadService', [
      'reload',
    ]);

    station2.runHostOnlyTests.and.returnValue(
      of(
        new MauiHostOnlyTestResponse({
          allPassed: true,
          summary: 'Host tests OK',
          mcuVersionStr: 'v1.0.0',
          pdcAppconfigVer: '00000318',
          hostOrientation: 'CC1',
          detectedHwrev: 2,
          hwrevOk: true,
          temperatureC: 35.5,
          tempOk: true,
          gpiosOk: true,
          gpioDetails: '19/19',
          voltagesOk: true,
          pp5000Mv: 5020,
          hostVbusMv: 5010,
          chgVbusMv: 19800,
        }),
      ),
    );
    station2.testHostReplug.and.returnValue(
      of(
        new MauiHostReplugTestResponse({
          allPassed: true,
          message: 'Replug OK',
          hostOrientation: 'CC2',
        }),
      ),
    );
    station2.testResetButton.and.callFake((_s, _p, action) =>
      of(
        action === 'check_initial_uptime'
          ? new MauiResetButtonTestResponse({
              success: true,
              uptimeSufficient: true,
              currentUptimeMs: '12000',
            })
          : new MauiResetButtonTestResponse({
              success: true,
              resetVerified: true,
              message: 'Reboot verified',
            }),
      ),
    );
    station2.testBslButton.and.returnValue(
      of(new MauiBslButtonTestResponse({success: true, peakBslMv: 3200})),
    );
    station2.runHostDutTests.and.returnValue(
      of(
        new MauiHostDutTestResponse({
          allPassed: true,
          h2hSpeed5gbps: true,
          dutOrientation: 'SBU1',
          negotiatedVoltageV: 20,
          negotiatedCurrentA: 5,
          chargerDisconnectedOk: true,
        }),
      ),
    );
    station2.runAdbTransferTest.and.returnValue(of(adbTransferPassed()));
    deviceMgmt.readConnectedDeviceSerial.and.returnValue(of(connected()));
    deviceMgmt.verifyScannedLabel.and.returnValue(
      of(new MauiVerifyLabelResponse({matches: true, message: 'Matched'})),
    );
    deviceMgmt.getDeviceLogsStatus.and.returnValue(
      of(new MauiLogsStatusResponse({directoryPath: '/var/log/x'})),
    );
    deviceMgmt.saveCardLog.and.returnValue(
      of(new MauiSaveLogResponse({success: true})),
    );
    deviceMgmt.submitFinalManufacturingResults.and.returnValue(
      of(
        new MauiFinalSubmissionResponse({
          success: true,
          mesTrackingId: 'MES-1',
          summaryFilePath: '/results/SMT_FUNCTIONAL_PASS.json',
          message: 'Exported JSON to /results/smt_functional/results/x.json',
        }),
      ),
    );

    await TestBed.configureTestingModule({
      imports: [MauiFunctionalSuiteComponent, NoopAnimationsModule],
      providers: [
        provideRouter([]),
        {provide: MauiStation2Service, useValue: station2},
        {provide: MauiDeviceMgmtService, useValue: deviceMgmt},
        {provide: MauiDialogService, useValue: dialog},
        {provide: PageReloadService, useValue: pageReload},
      ],
    }).compileComponents();
  });

  /** Creates the component inside the current fakeAsync zone. */
  function create(mode: 'smt' | 'fatp_qt0' = 'smt'): void {
    fixture = TestBed.createComponent(MauiFunctionalSuiteComponent);
    component = fixture.componentInstance;
    component.mode = mode;
    fixture.detectChanges();
    tick();
  }

  /** Lets the device poller see a new device state. */
  function pollDevice(res: MauiReadSerialResponse): void {
    deviceMgmt.readConnectedDeviceSerial.and.returnValue(of(res));
    tick(DEVICE_POLL_INTERVAL_MS);
  }

  /** Bypass callback passed to the most recent failure dialog. */
  function lastDialogBypass(): () => void {
    const bypass = dialog.openFailure.calls.mostRecent().args[3];
    expect(bypass).toBeDefined();
    return bypass!;
  }

  function finish(): void {
    flush();
    discardPeriodicTasks();
  }

  /** Runs Cards 1-9 with the configured (passing) responses. */
  function runAllCards(): void {
    component.runUsbCheck();
    tick(STABILIZATION_DELAY_MS);
    component.runHostReplug();
    tick(STABILIZATION_DELAY_MS);
    component.captureResetBaseline();
    component.verifyResetReboot();
    component.readBslButton();
    component.ledAmberChecked = true;
    component.ledGreenChecked = true;
    component.confirmLeds();
    component.runHostDutCharger();
    tick(STABILIZATION_DELAY_MS);
    component.runDutSbuFlip();
    tick(STABILIZATION_DELAY_MS);
    component.runChargerDisconnect();
    tick(STABILIZATION_DELAY_MS);
    component.runAdbTransfer();
  }

  it('polls the connected device', fakeAsync(() => {
    create();
    expect(component.deviceConnected).toBeTrue();
    expect(component.serialNumber).toBe(SERIAL);
    expect(component.devicePort).toBe('/dev/ttyUSB3');
    finish();
  }));

  it('keeps polling after a transient gRPC error', fakeAsync(() => {
    deviceMgmt.readConnectedDeviceSerial.and.returnValue(
      throwError(() => new Error('backend restarting')),
    );
    create();
    expect(component.deviceConnected).toBeFalse();
    expect(component.statusMessage).toBe('Device detection error.');

    pollDevice(connected());
    expect(component.deviceConnected).toBeTrue();
    finish();
  }));

  it('clears the device identity on a manual refresh with no device', fakeAsync(() => {
    create();
    expect(component.serialNumber).toBe(SERIAL);
    deviceMgmt.readConnectedDeviceSerial.and.returnValue(of(DISCONNECTED));
    component.refreshDeviceStatus();
    tick();
    // An empty port is sent as-is; the backend auto-detects the console.
    expect(component.devicePort).toBe('');
    expect(component.serialNumber).toBe('');
    expect(component.adbSerial).toBe('');
    expect(component.ftdiSerial).toBe('');
    finish();
  }));

  it('shows /dev/ttyUSB0 when no device port is known', fakeAsync(() => {
    deviceMgmt.readConnectedDeviceSerial.and.returnValue(
      of(
        new MauiReadSerialResponse({
          isConnected: true,
          serialNumber: SERIAL,
          ftdiIsProgrammed: true,
        }),
      ),
    );
    create();
    fixture.detectChanges();
    const text = (fixture.nativeElement as HTMLElement).querySelector(
      '.device-status-text',
    )?.textContent;
    expect(text?.replace(/\s+/g, ' ')).toContain('(Port: /dev/ttyUSB0)');
    finish();
  }));

  it('runs the full sequence, archives the report and reloads', fakeAsync(() => {
    create();
    runAllCards();
    expect(component.allCardsPassed).toBeTrue();
    expect(component.canSaveLogs).toBeFalse(); // Unit still connected.

    pollDevice(DISCONNECTED);
    expect(component.canSaveLogs).toBeTrue();
    component.saveLogs();

    const summary = deviceMgmt.saveCardLog.calls.mostRecent().args[2];
    expect(summary).toContain('OVERALL RESULT: PASS\n');
    expect(summary).toContain(
      'Card 9 (ADB Transfer, two ~400 MB random files, must finish within 60 s): PASSED',
    );
    expect(summary).toContain('push 50.0 MB/s, pull 50.0 MB/s, 800 MB, 40.0 s');
    const results =
      deviceMgmt.submitFinalManufacturingResults.calls.mostRecent().args[0];
    expect(results.serialNumber).toBe(SERIAL);
    expect(results.stationId).toBe('STATION_SMT_FUNCTIONAL');
    expect(results.hostOnlyPassed).toBeTrue();
    expect(results.hostDutPassed).toBeTrue();
    expect(results.mcuFlashed).toBeFalse();
    expect(results.ftdiProgrammed).toBeFalse();
    expect(results.pdcFlashed).toBeFalse();
    expect(results.stageStatuses).toEqual({
      host_usb_console_check: 'PASSED',
      host_cable_flip: 'PASSED',
      reset_button: 'PASSED',
      bsl_button: 'PASSED',
      visual_leds: 'PASSED',
      host_dut_charger: 'PASSED',
      dut_sbu_flip: 'PASSED',
      charger_disconnect: 'PASSED',
      adb_transfer_test: 'PASSED',
    });
    expect(component.logsSaved).toBeTrue();
    expect(component.finalMesTrackingId).toBe('MES-1');
    fixture.detectChanges();
    const page = (
      (fixture.nativeElement as HTMLElement).textContent || ''
    ).replace(/\s+/g, ' ');
    expect(page).toContain(
      'report archived to /results/SMT_FUNCTIONAL_PASS.json.',
    );
    expect(page).toContain('Exported JSON to /results/smt_functional/');

    expect(pageReload.reload).not.toHaveBeenCalled();
    tick(PAGE_RELOAD_DELAY_MS);
    expect(pageReload.reload).toHaveBeenCalledTimes(1);
    finish();
  }));

  it('waits for the stabilization delay before running a card', fakeAsync(() => {
    create();
    component.runUsbCheck();
    expect(component.cards.hostUsb.inProgress).toBeTrue();
    expect(station2.runHostOnlyTests).not.toHaveBeenCalled();
    tick(STABILIZATION_DELAY_MS);
    expect(station2.runHostOnlyTests).toHaveBeenCalledWith(
      SERIAL,
      '/dev/ttyUSB3',
    );
    expect(component.cards.hostUsb).toEqual({
      inProgress: false,
      done: true,
      status: 'PASSED',
    });
    finish();
  }));

  it('opens the failure dialog when a card fails', fakeAsync(() => {
    station2.runHostOnlyTests.and.returnValue(
      of(new MauiHostOnlyTestResponse({allPassed: false, summary: 'PDC down'})),
    );
    create();
    component.runUsbCheck();
    tick(STABILIZATION_DELAY_MS);

    expect(component.cards.hostUsb.status).toBe('FAILED');
    expect(component.cards.hostUsb.done).toBeFalse();
    const [title, message] = dialog.openFailure.calls.mostRecent().args;
    expect(title).toBe('Host USB & Console Check Failed');
    expect(message).toBe('PDC down');
    finish();
  }));

  it('retries a card from the failure dialog', fakeAsync(() => {
    station2.testBslButton.and.returnValues(
      of(new MauiBslButtonTestResponse({success: false})),
      of(new MauiBslButtonTestResponse({success: true})),
    );
    create();
    component.readBslButton();
    expect(component.cards.bsl.status).toBe('FAILED');

    const retry = dialog.openFailure.calls.mostRecent().args[2];
    retry!();
    expect(station2.testBslButton).toHaveBeenCalledTimes(2);
    expect(component.cards.bsl.status).toBe('PASSED');
    finish();
  }));

  it('marks a card FAILED when its RPC errors', fakeAsync(() => {
    station2.testBslButton.and.returnValue(
      throwError(() => new Error('serial port busy')),
    );
    create();
    component.readBslButton();

    expect(component.cards.bsl.status).toBe('FAILED');
    expect(component.cards.bsl.inProgress).toBeFalse();
    expect(dialog.openFailure.calls.mostRecent().args[0]).toBe(
      'BSL Button Test Error',
    );
    finish();
  }));

  it('unlocks the next card on bypass but reports the card as BYPASSED', fakeAsync(() => {
    station2.runHostOnlyTests.and.returnValue(
      of(new MauiHostOnlyTestResponse({allPassed: false})),
    );
    create();
    component.runUsbCheck();
    tick(STABILIZATION_DELAY_MS);
    lastDialogBypass()();

    expect(component.cards.hostUsb).toEqual({
      inProgress: false,
      done: true,
      status: 'BYPASSED',
    });

    // Finish the remaining cards and archive.
    component.runHostReplug();
    tick(STABILIZATION_DELAY_MS);
    component.captureResetBaseline();
    component.verifyResetReboot();
    component.readBslButton();
    component.bypassCard('leds');
    component.runHostDutCharger();
    tick(STABILIZATION_DELAY_MS);
    component.runDutSbuFlip();
    tick(STABILIZATION_DELAY_MS);
    component.runChargerDisconnect();
    tick(STABILIZATION_DELAY_MS);
    component.runAdbTransfer();
    pollDevice(DISCONNECTED);
    component.saveLogs();

    expect(component.allCardsPassed).toBeFalse();
    expect(component.overallVerdict).toBe('PASS_WITH_BYPASS');
    expect(deviceMgmt.saveCardLog.calls.mostRecent().args[2]).toContain(
      'OVERALL RESULT: PASS_WITH_BYPASS\n',
    );
    const results =
      deviceMgmt.submitFinalManufacturingResults.calls.mostRecent().args[0];
    expect(results.hostOnlyPassed).toBeFalse();
    expect(results.hostDutPassed).toBeTrue();
    expect(results.stageStatuses?.['host_usb_console_check']).toBe('BYPASSED');
    expect(results.stageStatuses?.['visual_leds']).toBe('BYPASSED');
    expect(component.ledAmberChecked).toBeTrue();
    finish();
  }));

  it('reports FAIL when any card failed or did not run', fakeAsync(() => {
    create();
    runAllCards();
    expect(component.overallVerdict).toBe('PASS');
    component.bypassCard('leds');
    expect(component.overallVerdict).toBe('PASS_WITH_BYPASS');
    component.cards.bsl.status = 'FAILED';
    expect(component.overallVerdict).toBe('FAIL');
    component.cards.bsl.status = 'NOT_RUN';
    expect(component.overallVerdict).toBe('FAIL');
    finish();
  }));

  it('unlocks log saving only after the ADB transfer card', fakeAsync(() => {
    create();
    runAllCards();
    expect(component.cards.adbTransfer.status).toBe('PASSED');
    expect(station2.runAdbTransferTest).toHaveBeenCalledOnceWith(
      SERIAL,
      SERIAL,
    );
    component.cards.adbTransfer = {
      inProgress: false,
      done: false,
      status: 'NOT_RUN',
    };
    pollDevice(DISCONNECTED);
    expect(component.cards.chargerDisconnect.done).toBeTrue();
    expect(component.canSaveLogs).toBeFalse();
    finish();
  }));

  it('reports a failed and bypassed ADB transfer as BYPASSED', fakeAsync(() => {
    station2.runAdbTransferTest.and.returnValue(
      of(
        new MauiAdbTransferTestResponse({
          success: false,
          message: 'Data corrupted on pull',
        }),
      ),
    );
    create();
    runAllCards();

    expect(component.cards.adbTransfer.status).toBe('FAILED');
    expect(component.cards.adbTransfer.done).toBeFalse();
    const [title, message] = dialog.openFailure.calls.mostRecent().args;
    expect(title).toBe('ADB Transfer Test Failed');
    expect(message).toBe('Data corrupted on pull');
    expect(component.adbTransferResult?.message).toBe('Data corrupted on pull');

    lastDialogBypass()();
    expect(component.cards.adbTransfer).toEqual({
      inProgress: false,
      done: true,
      status: 'BYPASSED',
    });
    pollDevice(DISCONNECTED);
    component.saveLogs();
    expect(deviceMgmt.saveCardLog.calls.mostRecent().args[2]).toContain(
      'Card 9 (ADB Transfer, two ~400 MB random files, must finish within 60 s): BYPASSED',
    );
    const results =
      deviceMgmt.submitFinalManufacturingResults.calls.mostRecent().args[0];
    expect(results.stageStatuses?.['adb_transfer_test']).toBe('BYPASSED');
    expect(component.overallVerdict).toBe('PASS_WITH_BYPASS');
    finish();
  }));

  it('marks the ADB transfer FAILED when its RPC errors', fakeAsync(() => {
    station2.runAdbTransferTest.and.returnValue(
      throwError(() => new Error('no ADB device')),
    );
    create();
    runAllCards();
    expect(component.cards.adbTransfer.status).toBe('FAILED');
    expect(component.cards.adbTransfer.inProgress).toBeFalse();
    expect(dialog.openFailure.calls.mostRecent().args[0]).toBe(
      'ADB Transfer Test Error',
    );
    expect(component.canSaveLogs).toBeFalse();
    finish();
  }));

  it('drops a pending ADB transfer when destroyed', fakeAsync(() => {
    const pending = new Subject<MauiAdbTransferTestResponse>();
    station2.runAdbTransferTest.and.returnValue(pending);
    create();
    component.runAdbTransfer();
    expect(pending.observed).toBeTrue();
    fixture.destroy();
    expect(pending.observed).toBeFalse();
    finish();
  }));

  it('uses the bypass baseline when the reset baseline is bypassed', fakeAsync(() => {
    station2.testResetButton.and.returnValue(
      of(new MauiResetButtonTestResponse({uptimeSufficient: false})),
    );
    create();
    component.captureResetBaseline();
    lastDialogBypass()();
    expect(component.resetBaselineCaptured).toBeTrue();
    expect(component.initialUptimeMs).toBe(15000);
    // The baseline is a sub-step; the RESET card itself is not decided yet.
    expect(component.cards.reset.status).toBe('NOT_RUN');
    finish();
  }));

  it('shows a failure and does not reload when saving the log fails', fakeAsync(() => {
    deviceMgmt.saveCardLog.and.returnValue(
      throwError(() => new Error('disk full')),
    );
    create();
    runAllCards();
    pollDevice(DISCONNECTED);
    component.saveLogs();

    expect(deviceMgmt.submitFinalManufacturingResults).not.toHaveBeenCalled();
    expect(component.logsSaved).toBeFalse();
    expect(component.savingLogs).toBeFalse();
    expect(component.canSaveLogs).toBeTrue();
    const [title, err, retry] = dialog.openFailure.calls.mostRecent().args;
    expect(title).toBe('Log Archival Failed');
    expect((err as Error).message).toBe('disk full');

    tick(PAGE_RELOAD_DELAY_MS);
    expect(pageReload.reload).not.toHaveBeenCalled();

    // Retry succeeds and only then reloads.
    deviceMgmt.saveCardLog.and.returnValue(
      of(new MauiSaveLogResponse({success: true})),
    );
    retry!();
    expect(component.logsSaved).toBeTrue();
    tick(PAGE_RELOAD_DELAY_MS);
    expect(pageReload.reload).toHaveBeenCalledTimes(1);
    finish();
  }));

  it('treats an unsuccessful MES submission as a failure', fakeAsync(() => {
    deviceMgmt.submitFinalManufacturingResults.and.returnValue(
      of(
        new MauiFinalSubmissionResponse({success: false, message: 'MES down'}),
      ),
    );
    create();
    runAllCards();
    pollDevice(DISCONNECTED);
    component.saveLogs();

    expect(component.logsSaved).toBeFalse();
    const err = dialog.openFailure.calls.mostRecent().args[1] as Error;
    expect(err.message).toBe('MES down');
    tick(PAGE_RELOAD_DELAY_MS);
    expect(pageReload.reload).not.toHaveBeenCalled();
    finish();
  }));

  it('refuses to save while the unit is still connected', fakeAsync(() => {
    create();
    runAllCards();
    component.saveLogs();
    expect(deviceMgmt.saveCardLog).not.toHaveBeenCalled();
    expect(dialog.openFailure.calls.mostRecent().args[0]).toBe(
      'Unit Still Connected',
    );
    finish();
  }));

  it('locks the serial once the sequence starts', fakeAsync(() => {
    create();
    component.runUsbCheck();
    tick(STABILIZATION_DELAY_MS);
    expect(component.lockedSerial).toBe(SERIAL);

    const statusBefore = component.statusMessage;

    const other = connected('MAUI-OTHER-999');
    other.ftdiIsProgrammed = false;
    other.ftdiPid = 0x6001;
    pollDevice(other);
    expect(component.unexpectedSerial).toBe('MAUI-OTHER-999');
    expect(component.otherUnitDetected).toBeTrue();
    expect(component.serialNumber).toBe(SERIAL);
    expect(component.adbSerial).toBe(SERIAL);
    expect(component.deviceProgrammed).toBeTrue();
    expect(component.ftdiPid).toBe('0x6002');
    expect(component.statusMessage).toBe(statusBefore);

    pollDevice(connected());
    expect(component.unexpectedSerial).toBe('');
    finish();
  }));

  it('blocks all run, bypass and save actions while another unit is connected', fakeAsync(() => {
    create();
    component.runUsbCheck();
    tick(STABILIZATION_DELAY_MS);
    pollDevice(connected('MAUI-OTHER-999'));
    fixture.detectChanges();

    const root = fixture.nativeElement as HTMLElement;
    expect(root.textContent).toContain('Different Unit Detected');
    const actionButtons = Array.from(
      root.querySelectorAll<HTMLButtonElement>('.actions-wrapper button'),
    ).filter(btn => !btn.textContent?.includes('Refresh Logs Checklist'));
    expect(actionButtons.length).toBeGreaterThan(0);
    for (const btn of actionButtons) {
      expect(btn.disabled)
        .withContext(btn.textContent || '')
        .toBeTrue();
    }

    component.runHostReplug();
    tick(STABILIZATION_DELAY_MS);
    expect(station2.testHostReplug).not.toHaveBeenCalled();
    component.captureResetBaseline();
    expect(station2.testResetButton).not.toHaveBeenCalled();
    component.bypassCard('hostFlip');
    expect(component.cards.hostFlip.status).toBe('NOT_RUN');
    component.ledAmberChecked = true;
    component.ledGreenChecked = true;
    component.confirmLeds();
    expect(component.cards.leds.status).toBe('NOT_RUN');
    component.saveLogs();
    expect(deviceMgmt.saveCardLog).not.toHaveBeenCalled();
    expect(component.canSaveLogs).toBeFalse();

    // The unit under test is back: testing continues.
    pollDevice(connected());
    component.runHostReplug();
    tick(STABILIZATION_DELAY_MS);
    expect(station2.testHostReplug).toHaveBeenCalledTimes(1);
    expect(component.cards.hostFlip.status).toBe('PASSED');
    finish();
  }));

  it('cancels pending card RPCs when destroyed', fakeAsync(() => {
    create();
    component.runUsbCheck();
    fixture.destroy();
    tick(STABILIZATION_DELAY_MS);
    expect(station2.runHostOnlyTests).not.toHaveBeenCalled();
    expect(dialog.openFailure).not.toHaveBeenCalled();
    finish();
  }));

  describe('in FATP QT0 mode', () => {
    it('verifies the barcode before unlocking Card 1', fakeAsync(() => {
      create('fatp_qt0');
      expect(component.barcodeMatches).toBeFalse();
      component.scannedBarcode = SERIAL;
      component.verifyBarcode();

      expect(deviceMgmt.verifyScannedLabel).toHaveBeenCalledWith(
        SERIAL,
        SERIAL,
        SERIAL,
        SERIAL,
      );
      expect(component.barcodeMatches).toBeTrue();
      expect(component.cards.barcode.status).toBe('PASSED');
      expect(component.lockedSerial).toBe(SERIAL);
      finish();
    }));

    it('ignores a second scan while a verification is running', fakeAsync(() => {
      create('fatp_qt0');
      component.cards.barcode.inProgress = true;
      component.scannedBarcode = SERIAL;
      component.verifyBarcode();
      expect(deviceMgmt.verifyScannedLabel).not.toHaveBeenCalled();
      finish();
    }));

    it('reports a bypassed barcode as not verified', fakeAsync(() => {
      deviceMgmt.verifyScannedLabel.and.returnValue(
        of(new MauiVerifyLabelResponse({matches: false, message: 'Mismatch'})),
      );
      create('fatp_qt0');
      component.scannedBarcode = 'WRONG-LABEL';
      component.verifyBarcode();
      expect(component.barcodeCheckFailed).toBeTrue();
      expect(dialog.openFailure.calls.mostRecent().args[0]).toBe(
        'Barcode Serial Mismatch',
      );
      lastDialogBypass()();
      expect(component.barcodeMatches).toBeTrue();
      expect(component.barcodeMessage).toBe(
        'Barcode verification bypassed by operator.',
      );

      runAllCards();
      pollDevice(DISCONNECTED);
      component.saveLogs();
      const results =
        deviceMgmt.submitFinalManufacturingResults.calls.mostRecent().args[0];
      expect(results.stationId).toBe('STATION_FATP_QT0');
      expect(results.labelVerified).toBeFalse();
      expect(results.scannedBarcode).toBe('WRONG-LABEL');
      expect(results.stageStatuses?.['outer_barcode_verification']).toBe(
        'BYPASSED',
      );
      finish();
    }));
  });
});
