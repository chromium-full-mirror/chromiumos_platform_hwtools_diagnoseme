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
import {PageReloadService} from '../page-reload.service';
import {
  DEVICE_POLL_INTERVAL_MS,
  PAGE_RELOAD_DELAY_MS,
  STABILIZATION_DELAY_MS,
} from '../maui-constants';
import {
  MauiAdbTransferTestResponse,
  MauiBslButtonProgress,
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

  /** Runs Cards 1-9 (ADB before charger disconnect) with the configured responses. */
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
    component.runAdbTransfer();
    component.runChargerDisconnect();
    tick(STABILIZATION_DELAY_MS);
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
    expect(summary).not.toContain('Not passed');
    expect(summary).toContain(
      'Card 8 (ADB Transfer, two ~400 MB random files): PASSED',
    );
    expect(summary).toContain(
      '2 files 763 MiB, push 50.0 MB/s, pull 50.0 MB/s',
    );
    expect(summary).toContain('Card 9 (Charger Disconnected): PASSED');
    expect(summary.indexOf('Card 8 (ADB')).toBeLessThan(
      summary.indexOf('Card 9 (Charger'),
    );
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
      adb_transfer_test: 'PASSED',
      charger_disconnect: 'PASSED',
    });
    // The SMT station does not verify the label.
    expect(results.labelVerified).toBeFalse();
    expect(component.logsSaved).toBeTrue();
    expect(component.finalPassed).toBeTrue();
    expect(component.finalMesTrackingId).toBe('MES-1');
    fixture.detectChanges();
    expect(
      fixture.nativeElement.querySelector('#func-final-pass').textContent,
    ).toContain('PASSED');
    expect(fixture.nativeElement.querySelector('#func-next-unit')).toBeNull();
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
    component.runAdbTransfer();
    component.runChargerDisconnect();
    tick(STABILIZATION_DELAY_MS);
    pollDevice(DISCONNECTED);
    component.saveLogs();

    expect(component.allCardsPassed).toBeFalse();
    expect(component.overallVerdict).toBe('PASS_WITH_BYPASS');
    const summary = deviceMgmt.saveCardLog.calls.mostRecent().args[2];
    expect(summary).toContain(
      'Not passed: Card 1 (Host USB & Console): BYPASSED, Card 5 (Visual LEDs): BYPASSED\n',
    );
    expect(summary).toContain('OVERALL RESULT: PASS_WITH_BYPASS\n');
    const results =
      deviceMgmt.submitFinalManufacturingResults.calls.mostRecent().args[0];
    expect(results.hostOnlyPassed).toBeFalse();
    expect(results.hostDutPassed).toBeTrue();
    expect(results.stageStatuses?.['host_usb_console_check']).toBe('BYPASSED');
    expect(results.stageStatuses?.['visual_leds']).toBe('BYPASSED');
    expect(component.ledAmberChecked).toBeTrue();

    // Bypassed cards fail the unit: no automatic reload, "Next Unit" instead.
    expect(component.logsSaved).toBeTrue();
    expect(component.finalPassed).toBeFalse();
    fixture.detectChanges();
    const el: HTMLElement = fixture.nativeElement;
    expect(el.querySelector('#func-final-pass')).toBeNull();
    expect(el.querySelector('#func-final-fail')?.textContent).toContain(
      'Card 5 (Visual LEDs): BYPASSED',
    );
    tick(PAGE_RELOAD_DELAY_MS);
    expect(pageReload.reload).not.toHaveBeenCalled();
    (el.querySelector('#func-next-unit') as HTMLButtonElement).click();
    expect(pageReload.reload).toHaveBeenCalledTimes(1);
    finish();
  }));

  it('fails a unit with a card that was not run', fakeAsync(() => {
    create();
    runAllCards();
    component.cards.leds.status = 'NOT_RUN';
    pollDevice(DISCONNECTED);
    component.saveLogs();
    expect(component.finalPassed).toBeFalse();
    expect(component.finalNotPassedCards).toEqual([
      'Card 5 (Visual LEDs): NOT_RUN',
    ]);
    expect(deviceMgmt.saveCardLog.calls.mostRecent().args[2]).toContain(
      'OVERALL RESULT: FAIL\n',
    );
    tick(PAGE_RELOAD_DELAY_MS);
    expect(pageReload.reload).not.toHaveBeenCalled();
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

  it('unlocks log saving only after the charger disconnect card', fakeAsync(() => {
    create();
    runAllCards();
    expect(component.cards.adbTransfer.status).toBe('PASSED');
    expect(station2.runAdbTransferTest).toHaveBeenCalledOnceWith(
      SERIAL,
      SERIAL,
    );
    component.cards.chargerDisconnect = {
      inProgress: false,
      done: false,
      status: 'NOT_RUN',
    };
    pollDevice(DISCONNECTED);
    expect(component.cards.adbTransfer.done).toBeTrue();
    expect(component.canSaveLogs).toBeFalse();
    finish();
  }));

  it('opens the charger card after the ADB card and the logs after it', fakeAsync(() => {
    create();
    fixture.detectChanges();
    const disabled = (id: string) =>
      !!(fixture.nativeElement as HTMLElement).querySelector(
        `#${id} .disabled-card`,
      );
    expect(disabled('func-card-adb')).toBeTrue();
    component.cards.dutSbuFlip.done = true;
    fixture.detectChanges();
    expect(disabled('func-card-adb')).toBeFalse();
    expect(disabled('func-card-charger')).toBeTrue();
    component.bypassCard('adbTransfer');
    fixture.detectChanges();
    expect(disabled('func-card-charger')).toBeFalse();
    expect(disabled('func-card-logs')).toBeTrue();
    deviceMgmt.getDeviceLogsStatus.calls.reset();
    component.bypassCard('chargerDisconnect');
    fixture.detectChanges();
    expect(disabled('func-card-logs')).toBeFalse();
    expect(deviceMgmt.getDeviceLogsStatus).toHaveBeenCalled();
    finish();
  }));

  it('asks to unplug all cables before the charger card', fakeAsync(() => {
    create();
    const root = fixture.nativeElement as HTMLElement;
    const text = root
      .querySelector('#func-card-charger')!
      .textContent!.replace(/\s+/g, ' ');
    expect(text).toContain('Unplug ALL cables from Maui');
    expect(text).toContain('connect only the Host and DUT cables again');
    expect(
      root.querySelector('#func-card-adb')!.textContent!.replace(/\s+/g, ' '),
    ).toContain('within the time limit (40 s) on a 5 Gbps link');
    const ids = Array.from(root.querySelectorAll('[id^="func-card-"]')).map(
      e => e.id,
    );
    expect(ids.indexOf('func-card-7')).toBeLessThan(
      ids.indexOf('func-card-adb'),
    );
    expect(ids.indexOf('func-card-adb')).toBeLessThan(
      ids.indexOf('func-card-charger'),
    );
    expect(ids.indexOf('func-card-charger')).toBeLessThan(
      ids.indexOf('func-card-logs'),
    );
    finish();
  }));

  it('summarizes the ADB transfer', fakeAsync(() => {
    create();
    const t = new MauiAdbTransferTestResponse({
      ...adbTransferPassed().toObject(),
      fileSizeBytes: '838860800',
      pushMbPerS: 57.04,
      pullMbPerS: 88.2,
      transferSeconds: 25.13,
      deadlineSeconds: 40,
    });
    expect(component.adbSummary(t)).toBe(
      'transfer 25.1 s / limit 40 s, 2 files 800 MiB, push 57.0 MB/s, pull 88.2 MB/s',
    );
    t.hostLinkMbps = 5000;
    t.dutLinkMbps = 5000;
    expect(component.adbSummary(t)).toContain(
      'pull 88.2 MB/s, link host/DUT 5000 Mb/s',
    );
    t.dutLinkMbps = 480;
    expect(component.adbSummary(t)).toContain('link host/DUT 5000/480 Mb/s');
    expect(component.adbSummary(new MauiAdbTransferTestResponse())).toBe(
      'push 0.0 MB/s, pull 0.0 MB/s',
    );
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
      'Card 8 (ADB Transfer, two ~400 MB random files): BYPASSED',
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
      expect(component.notPassedCards).toEqual(['Barcode: BYPASSED']);
      expect(component.finalPassed).toBeFalse();
      finish();
    }));

    it('verifies the label only with a passed barcode card', fakeAsync(() => {
      create('fatp_qt0');
      component.scannedBarcode = SERIAL;
      component.verifyBarcode();
      runAllCards();
      pollDevice(DISCONNECTED);
      component.saveLogs();
      const results =
        deviceMgmt.submitFinalManufacturingResults.calls.mostRecent().args[0];
      expect(results.labelVerified).toBeTrue();
      expect(component.finalPassed).toBeTrue();
      finish();
    }));
  });
});

describe('MauiFunctionalSuiteComponent hardware revisions', () => {
  let component: MauiFunctionalSuiteComponent;
  let fixture: ComponentFixture<MauiFunctionalSuiteComponent>;
  let station2: jasmine.SpyObj<MauiStation2Service>;
  let dialog: jasmine.SpyObj<MauiDialogService>;
  let deviceMgmt: jasmine.SpyObj<MauiDeviceMgmtService>;
  let mcu: jasmine.SpyObj<MauiMcuService>;
  let pageReload: jasmine.SpyObj<PageReloadService>;

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
    tick();
  }

  function text(selector: string): string {
    return (
      (fixture.nativeElement as HTMLElement).querySelector(selector)
        ?.textContent ?? ''
    ).replace(/\s+/g, ' ');
  }

  function finish(): void {
    flush();
    discardPeriodicTasks();
  }

  beforeEach(async () => {
    station2 = jasmine.createSpyObj('MauiStation2Service', [
      'runHostOnlyTests',
      'testHostReplug',
      'testBslButton',
      'streamBslButtonTest',
    ]);
    dialog = jasmine.createSpyObj('MauiDialogService', ['openFailure']);
    deviceMgmt = jasmine.createSpyObj('MauiDeviceMgmtService', [
      'readConnectedDeviceSerial',
      'saveCardLog',
      'submitFinalManufacturingResults',
      'getDeviceLogsStatus',
    ]);
    deviceMgmt.readConnectedDeviceSerial.and.returnValue(
      of(
        new MauiReadSerialResponse({
          isConnected: true,
          serialNumber: SN,
          devicePort: '/dev/ttyACM0',
        }),
      ),
    );
    deviceMgmt.saveCardLog.and.returnValue(
      of(new MauiSaveLogResponse({success: true})),
    );
    deviceMgmt.submitFinalManufacturingResults.and.returnValue(
      of(new MauiFinalSubmissionResponse({success: true})),
    );
    deviceMgmt.getDeviceLogsStatus.and.returnValue(
      of(new MauiLogsStatusResponse()),
    );
    mcu = jasmine.createSpyObj('MauiMcuService', [
      'provisionMcuSerial',
      'checkH2hSpiSerial',
    ]);
    pageReload = jasmine.createSpyObj<PageReloadService>('PageReloadService', [
      'reload',
    ]);
    await TestBed.configureTestingModule({
      imports: [MauiFunctionalSuiteComponent, NoopAnimationsModule],
      providers: [
        provideRouter([]),
        {provide: MauiStation2Service, useValue: station2},
        {provide: MauiDeviceMgmtService, useValue: deviceMgmt},
        {provide: MauiMcuService, useValue: mcu},
        {provide: MauiDialogService, useValue: dialog},
        {provide: PageReloadService, useValue: pageReload},
      ],
    }).compileComponents();
  });

  it('shows the expected hwrev reported by the server', fakeAsync(() => {
    create('v1.2');
    component.cards.serialProvision.done = true;
    component.cards.h2hSpi.done = true;
    station2.runHostOnlyTests.and.returnValue(
      of(new MauiHostOnlyTestResponse({...hostOnly, expectedHwrev: '3 or 4'})),
    );
    component.runUsbCheck();
    tick(STABILIZATION_DELAY_MS);
    fixture.detectChanges();
    expect(component.hostUsbCheck.expectedHwrev).toBe('3 or 4');
    expect(text('#func-card-1')).toContain('3 (Expected: 3 or 4)');
    expect(text('#func-card-1')).toContain('All 29 GPIOs verified');
    finish();
  }));

  it('falls back to the profile when the server sends no expected hwrev', fakeAsync(() => {
    create('v1.1');
    station2.runHostOnlyTests.and.returnValue(
      of(new MauiHostOnlyTestResponse({...hostOnly, detectedHwrev: 2})),
    );
    component.runUsbCheck();
    tick(STABILIZATION_DELAY_MS);
    fixture.detectChanges();
    expect(component.hostUsbCheck.expectedHwrev).toBe('2');
    expect(text('#func-card-1')).toContain('2 (Expected: 2)');
    expect(text('#func-card-1')).toContain('19/19 Verified');
    finish();
  }));

  it('uses the expected hwrev of the server on the flip card', fakeAsync(() => {
    create('v1.2');
    station2.testHostReplug.and.returnValue(
      of(
        new MauiHostReplugTestResponse({
          ...hostOnly,
          message: 'ok',
          expectedHwrev: '3',
        }),
      ),
    );
    component.runHostReplug();
    tick(STABILIZATION_DELAY_MS);
    expect(component.hostFlipCheck.expectedHwrev).toBe('3');
    finish();
  }));

  it('shows the v1.2 LED texts', fakeAsync(() => {
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
    finish();
  }));

  it('keeps the v1.1 LED texts', fakeAsync(() => {
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
    finish();
  }));

  it('shows the results directory of the revision', fakeAsync(() => {
    create('v1.2');
    expect(component.resultsDir).toBe('/var/log/diagnoseme/maui_v12_results');
    expect(component.archiveLocation).toBe(
      '/var/log/diagnoseme/maui_v12_results/',
    );
    fixture.destroy();
    create('v1.1');
    expect(component.resultsDir).toBe('/var/log/diagnoseme/maui_v11_results');
    finish();
  }));

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

  it('shows the H2H SPI card right after the serial card on v1.2', fakeAsync(() => {
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
    finish();
  }));

  it('numbers the v1.2 cards after the serial and H2H SPI cards', fakeAsync(() => {
    create('v1.2');
    expect(text('#func-card-1')).toContain(
      '3. Host USB & Console Verification',
    );
    expect(text('#func-card-adb')).toContain(
      '10. ADB Data Transfer Reliability',
    );
    expect(text('#func-card-charger')).toContain(
      '11. Charger Disconnect Verification',
    );
    expect(text('#func-card-logs')).toContain(
      '12. SMT Log Completion & Archival',
    );
    expect(component.bslCardTitle).toBe(
      '6. Interactive BSL Button Test (GPIO)',
    );
    finish();
  }));

  it('keeps the v1.1 card numbers', fakeAsync(() => {
    create('v1.1');
    expect(text('#func-card-1')).toContain(
      '1. Host USB & Console Verification',
    );
    expect(text('#func-card-adb')).toContain(
      '8. ADB Data Transfer Reliability',
    );
    expect(text('#func-card-charger')).toContain(
      '9. Charger Disconnect Verification',
    );
    expect(text('#func-card-logs')).toContain(
      '10. SMT Log Completion & Archival',
    );
    expect(component.bslCardTitle).toBe(
      '4. Interactive BSL Button Test (ADC Status)',
    );
    finish();
  }));

  it('has no serial or H2H SPI card on v1.1', fakeAsync(() => {
    create('v1.1');
    expect(cardIds()).not.toContain('func-card-serial');
    expect(cardIds()).not.toContain('func-card-h2h-spi');
    expect(component.serialCardsDone).toBeTrue();
    expect(card1Disabled()).toBeFalse();
    finish();
  }));

  it('enables Card 1 only when the serial and H2H steps are done', fakeAsync(() => {
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
    finish();
  }));

  it('locks the serial and H2H SPI cards while another test runs', fakeAsync(() => {
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
    component.cards.hostUsb.inProgress = true;
    fixture.detectChanges();
    expect(serialCard.locked).toBeTrue();
    expect(h2hCard.locked).toBeTrue();

    // A different unit (wrong-unit guard) locks both cards too.
    component.cards.hostUsb.inProgress = false;
    component.unexpectedSerial = 'MAUI-OTHER-999';
    fixture.detectChanges();
    expect(serialCard.locked).toBeTrue();
    expect(h2hCard.locked).toBeTrue();
    finish();
  }));

  it('runs the H2H SPI check for the detected board', fakeAsync(() => {
    create('v1.2');
    expect(component.serialNumber).toBe(SN);
    expect(component.devicePort).toBe('/dev/ttyACM0');
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
    expect(component.cards.h2hSpi.status).toBe('PASSED');
    expect(component.h2hSpiSerial).toBe(SN);
    expect(component.cards.h2hSpi.done).toBeTrue();
    finish();
  }));

  it('reports the v1.2 serial cards in the summary', fakeAsync(() => {
    create('v1.2');
    component.onSerialProvisionStatus({status: 'PASSED', serial: SN});
    component.onH2hSpiStatus({status: 'PASSED', serial: SN});
    component.cards.chargerDisconnect.done = true;
    component.deviceConnected = false;
    component.saveLogs();
    const summary = deviceMgmt.saveCardLog.calls.mostRecent().args[2];
    expect(summary).toContain('Card 1 (Serial Provisioning): PASSED');
    expect(summary).toContain(
      `Card 2 (H2H SPI Flash Access): PASSED (SPI serial ${SN})`,
    );
    expect(summary).toContain('Card 3 (Host USB & Console): ');
    expect(summary).toContain(
      'Card 6 (BSL Button GPIO, released -> pressed and held -> released): NOT_RUN\n',
    );
    expect(summary).toContain('Card 10 (ADB Transfer, two ~400 MB');
    expect(summary).toContain('Card 11 (Charger Disconnected): ');
    expect(summary).not.toContain('Card 0');
    finish();
  }));

  it('keeps the v1.1 summary without serial cards', fakeAsync(() => {
    create('v1.1');
    component.cards.chargerDisconnect.done = true;
    component.deviceConnected = false;
    component.saveLogs();
    const summary = deviceMgmt.saveCardLog.calls.mostRecent().args[2];
    expect(summary).not.toContain('Card 0');
    expect(summary).not.toContain('Serial Provisioning');
    expect(summary).toContain('Card 1 (Host USB & Console): ');
    expect(summary).toContain(
      'Card 4 (BSL Button ADC): NOT_RUN (Peak BSL: 0 mV)',
    );
    expect(summary).toContain('Card 8 (ADB Transfer, two ~400 MB');
    expect(summary).toContain('Card 9 (Charger Disconnected): ');
    finish();
  }));

  it('reports bypassed serial and H2H SPI cards as BYPASSED', fakeAsync(() => {
    create('v1.2');
    component.onSerialProvisionStatus({status: 'FAILED', serial: ''});
    component.onSerialProvisionStatus({status: 'BYPASSED', serial: ''});
    component.onH2hSpiStatus({status: 'BYPASSED', serial: ''});
    expect(component.serialCardsDone).toBeTrue();
    expect(component.stageStatuses()).toEqual(
      jasmine.objectContaining({
        mcu_serial_provisioning: 'BYPASSED',
        h2h_spi_check: 'BYPASSED',
      }),
    );
    expect(component.notPassedCards).toContain(
      'Card 1 (Serial Provisioning): BYPASSED',
    );
    expect(component.notPassedCards).toContain(
      'Card 2 (H2H SPI Flash Access): BYPASSED',
    );
    // No log is written from the UI; the server merges the stage statuses.
    expect(deviceMgmt.saveCardLog).not.toHaveBeenCalled();
    finish();
  }));

  function bslProgress(
    phase: number,
    prompt: string,
    extra: Partial<MauiBslButtonProgress.AsObject> = {},
  ): MauiBslButtonProgress {
    return new MauiBslButtonProgress({
      phase,
      prompt,
      secondsLeft: 1.5,
      level: 0,
      done: false,
      success: false,
      message: '',
      failedPhase: 0,
      ...extra,
    });
  }

  function stepClasses(): string[] {
    return Array.from(
      (fixture.nativeElement as HTMLElement).querySelectorAll(
        '#func-bsl-steps .bsl-step',
      ),
    ).map(
      e =>
        ['active', 'done', 'failed'].find(c =>
          e.classList.contains(`bsl-step-${c}`),
        ) ?? '',
    );
  }

  it('shows the 3 BSL steps with the live prompt on v1.2', fakeAsync(() => {
    create('v1.2');
    const stream = new Subject<MauiBslButtonProgress>();
    station2.streamBslButtonTest.and.returnValue(stream);
    component.cards.reset.done = true;
    fixture.detectChanges();
    expect(text('#func-card-4')).toContain('Start BSL Check');
    expect(text('#func-card-4')).toContain('do NOT touch the BSL button');
    component.readBslButton();
    expect(station2.streamBslButtonTest).toHaveBeenCalledWith(
      SN,
      '/dev/ttyACM0',
    );
    expect(station2.testBslButton).not.toHaveBeenCalled();
    stream.next(bslProgress(1, 'Do not touch the BSL button'));
    fixture.detectChanges();
    expect(text('#func-bsl-prompt')).toContain('Step 1 of 3');
    expect(text('#func-bsl-prompt')).toContain('Do not touch the BSL button');
    expect(text('#func-bsl-prompt')).toContain('1.5 s');
    expect(stepClasses()).toEqual(['active', '', '']);
    stream.next(
      bslProgress(2, 'Press and HOLD the BSL button now', {secondsLeft: 7.25}),
    );
    fixture.detectChanges();
    expect(text('#func-bsl-prompt')).toContain(
      'Press and HOLD the BSL button now',
    );
    expect(text('#func-bsl-prompt')).toContain('7.3 s');
    expect(stepClasses()).toEqual(['done', 'active', '']);
    stream.next(bslProgress(3, 'Release the BSL button'));
    stream.next(
      bslProgress(0, 'BSL button OK', {
        done: true,
        success: true,
        message: 'BSL Button Test PASSED!',
      }),
    );
    stream.complete();
    fixture.detectChanges();
    expect(component.cards.bsl.status).toBe('PASSED');
    expect(text('#func-card-4')).not.toContain('Step 1 of 3');
    expect(stepClasses()).toEqual(['done', 'done', 'done']);
    expect(dialog.openFailure).not.toHaveBeenCalled();
    finish();
  }));

  it('shows the failed BSL step and offers retry and bypass', fakeAsync(() => {
    create('v1.2');
    const message =
      'BSL Button Test FAILED (step 1, Do not touch): BSL signal reads 1 ' +
      'without pressing - R0714 missing or line floating?';
    station2.streamBslButtonTest.and.returnValue(
      of(
        bslProgress(1, 'BSL signal reads 1 without pressing', {
          done: true,
          level: 1,
          failedPhase: 1,
          message,
        }),
      ),
    );
    component.readBslButton();
    fixture.detectChanges();
    expect(component.cards.bsl.status).toBe('FAILED');
    expect(component.bslFailedPhase).toBe(1);
    expect(text('#func-bsl-failed-step')).toContain('Step 1 of 3');
    expect(text('#func-card-4')).toContain('R0714 missing or line floating?');
    expect(stepClasses()).toEqual(['failed', '', '']);
    expect(dialog.openFailure).toHaveBeenCalledWith(
      'BSL Button Test Failed',
      message,
      jasmine.any(Function),
      jasmine.any(Function),
    );
    const bypass = dialog.openFailure.calls.mostRecent().args[3]!;
    bypass();
    expect(component.cards.bsl.done).toBeTrue();
    expect(component.cards.bsl.status).toBe('BYPASSED');
    finish();
  }));

  it('fails the BSL card when the stream ends without a result', fakeAsync(() => {
    create('v1.2');
    station2.streamBslButtonTest.and.returnValue(
      of(bslProgress(2, 'Press and HOLD the BSL button now')),
    );
    component.readBslButton();
    expect(component.cards.bsl.status).toBe('FAILED');
    expect(component.cards.bsl.inProgress).toBeFalse();
    expect(dialog.openFailure.calls.mostRecent().args[1]).toContain(
      'ended without a result',
    );
    finish();
  }));

  it('keeps the v1.1 BSL ADC test', fakeAsync(() => {
    create('v1.1');
    station2.testBslButton.and.returnValue(
      of(
        new MauiBslButtonTestResponse({
          success: true,
          peakBslMv: 3100,
          message: 'BSL ok',
        }),
      ),
    );
    component.cards.reset.done = true;
    component.readBslButton();
    fixture.detectChanges();
    expect(station2.streamBslButtonTest).not.toHaveBeenCalled();
    expect(component.cards.bsl.status).toBe('PASSED');
    expect(text('#func-card-4')).toContain('Read BSL ADC');
    expect(text('#func-card-4')).toContain('3100 mV');
    expect(text('#func-card-4')).toContain('While holding, click');
    finish();
  }));

  it('keeps hiding a 0 mV BSL reading on v1.1', fakeAsync(() => {
    create('v1.1');
    station2.testBslButton.and.returnValue(
      of(
        new MauiBslButtonTestResponse({
          success: false,
          peakBslMv: 0,
          message: 'BSL failed',
        }),
      ),
    );
    component.readBslButton();
    fixture.detectChanges();
    expect(text('#func-card-4')).not.toContain('ADC Voltage:');
    finish();
  }));
});
