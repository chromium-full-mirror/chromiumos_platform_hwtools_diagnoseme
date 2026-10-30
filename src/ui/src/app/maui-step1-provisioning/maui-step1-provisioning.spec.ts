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
import {MauiStep1ProvisioningComponent} from './maui-step1-provisioning';
import {MauiStation1Service} from '../services/maui-station1.service';
import {MauiPdcService} from '../services/maui-pdc.service';
import {MauiDeviceMgmtService} from '../services/maui-device-mgmt.service';
import {MauiDialogService} from '../maui-common/maui-dialog.service';
import {PageReloadService} from '../maui-common/page-reload.service';
import {
  MCU_SETTLE_DELAY_MS,
  PAGE_RELOAD_DELAY_MS,
} from '../maui-common/maui-constants';
import {
  MauiBslDiscoveryResponse,
  MauiConnectedPdcDevicesResponse,
  MauiFinalSubmissionResponse,
  MauiPdcDeviceInfo,
  MauiProgramFtdiResponse,
  MauiProgramMcuResponse,
  MauiProgramPdcProgressResponse,
  MauiSaveLogResponse,
  MauiTopologyDiscoveryResponse,
} from '../../proto/maui-manufacturing.pb';

function bslDevice(port: string): MauiBslDiscoveryResponse {
  return new MauiBslDiscoveryResponse({
    bslDetected: true,
    devicePort: port,
    pid: 0x6001,
    autoFlashEligible: true,
    ftdiSerial: `FT-${port.slice(-1)}`,
  });
}

describe('MauiStep1ProvisioningComponent', () => {
  let fixture: ComponentFixture<MauiStep1ProvisioningComponent>;
  let component: MauiStep1ProvisioningComponent;
  let station1: jasmine.SpyObj<MauiStation1Service>;
  let pdc: jasmine.SpyObj<MauiPdcService>;
  let deviceMgmt: jasmine.SpyObj<MauiDeviceMgmtService>;
  let dialog: jasmine.SpyObj<MauiDialogService>;
  let pageReload: jasmine.SpyObj<PageReloadService>;
  let discovery$: Subject<MauiBslDiscoveryResponse>;

  beforeEach(async () => {
    discovery$ = new Subject<MauiBslDiscoveryResponse>();
    station1 = jasmine.createSpyObj<MauiStation1Service>(
      'MauiStation1Service',
      [
        'streamBslAutoDiscovery',
        'autoProgramMcuBsl',
        'discoverTopologyAndSerial',
        'programFtdiEeprom',
      ],
    );
    pdc = jasmine.createSpyObj<MauiPdcService>('MauiPdcService', [
      'discoverConnectedPdcDevices',
      'programSinglePdc',
    ]);
    deviceMgmt = jasmine.createSpyObj<MauiDeviceMgmtService>(
      'MauiDeviceMgmtService',
      ['saveCardLog', 'submitFinalManufacturingResults'],
    );
    dialog = jasmine.createSpyObj<MauiDialogService>('MauiDialogService', [
      'openFailure',
    ]);
    pageReload = jasmine.createSpyObj<PageReloadService>('PageReloadService', [
      'reload',
    ]);

    station1.streamBslAutoDiscovery.and.returnValue(discovery$);
    station1.autoProgramMcuBsl.and.returnValue(
      of(new MauiProgramMcuResponse({success: true, message: 'MCU ok'})),
    );
    station1.discoverTopologyAndSerial.and.callFake((port = '') =>
      of(
        new MauiTopologyDiscoveryResponse({
          success: true,
          ftdiDevNode: port,
          readSerialNumber: `MAUI-${port.slice(-1)}`,
          ftdiCurrentSerial: `FT-${port.slice(-1)}`,
          ftdiCurrentPid: 0x6001,
        }),
      ),
    );
    station1.programFtdiEeprom.and.returnValue(
      of(new MauiProgramFtdiResponse({success: true})),
    );
    pdc.programSinglePdc.and.returnValue(
      of(
        new MauiProgramPdcProgressResponse({
          isComplete: true,
          progressPercent: 100,
        }),
      ),
    );
    pdc.discoverConnectedPdcDevices.and.returnValue(
      of(new MauiConnectedPdcDevicesResponse({devices: []})),
    );
    deviceMgmt.saveCardLog.and.returnValue(
      of(new MauiSaveLogResponse({success: true})),
    );
    deviceMgmt.submitFinalManufacturingResults.and.returnValue(
      of(new MauiFinalSubmissionResponse({success: true})),
    );

    await TestBed.configureTestingModule({
      imports: [MauiStep1ProvisioningComponent, NoopAnimationsModule],
      providers: [
        provideRouter([]),
        {provide: MauiStation1Service, useValue: station1},
        {provide: MauiPdcService, useValue: pdc},
        {provide: MauiDeviceMgmtService, useValue: deviceMgmt},
        {provide: MauiDialogService, useValue: dialog},
        {provide: PageReloadService, useValue: pageReload},
      ],
    }).compileComponents();
  });

  function create(): void {
    fixture = TestBed.createComponent(MauiStep1ProvisioningComponent);
    component = fixture.componentInstance;
    fixture.detectChanges();
    tick();
  }

  function finish(): void {
    flush();
    discardPeriodicTasks();
  }

  it('auto-runs the pipeline (MCU -> PDC -> FTDI) on a detected board', fakeAsync(() => {
    create();
    discovery$.next(bslDevice('/dev/ttyUSB0'));
    const slot = component.slots[0];

    expect(station1.autoProgramMcuBsl).toHaveBeenCalledWith(
      '/dev/ttyUSB0',
      'stable',
      '',
    );
    expect(slot.stage).toBe('MCU_FLASHING');
    expect(pdc.programSinglePdc).not.toHaveBeenCalled();

    tick(MCU_SETTLE_DELAY_MS);

    expect(pdc.programSinglePdc).toHaveBeenCalled();
    expect(station1.programFtdiEeprom).toHaveBeenCalledWith(
      'MAUI-0',
      '/dev/ttyUSB0',
      0x0403,
      0x6002,
      'Maui Serial Console',
      'FT-0',
      0x6001,
    );
    expect(slot.stage).toBe('PROVISIONED');
    expect(slot.stepStatus).toEqual({
      mcu: 'PASSED',
      pdc: 'PASSED',
      ftdi: 'PASSED',
    });
    expect(component.pipelineCardPassed).toBeTrue();
    finish();
  }));

  it('allocates separate slots for multiple boards', fakeAsync(() => {
    create();
    discovery$.next(bslDevice('/dev/ttyUSB0'));
    discovery$.next(bslDevice('/dev/ttyUSB1'));

    expect(component.slots[0].devicePort).toBe('/dev/ttyUSB0');
    expect(component.slots[1].devicePort).toBe('/dev/ttyUSB1');
    expect(component.slots[1].isDetected).toBeTrue();
    expect(station1.autoProgramMcuBsl).toHaveBeenCalledTimes(2);
    finish();
  }));

  it('records the failing step when PDC flashing fails', fakeAsync(() => {
    pdc.programSinglePdc.and.returnValue(
      of(
        new MauiProgramPdcProgressResponse({
          isComplete: true,
          errorMessage: 'I2C NAK',
        }),
      ),
    );
    create();
    discovery$.next(bslDevice('/dev/ttyUSB0'));
    tick(MCU_SETTLE_DELAY_MS);
    const slot = component.slots[0];

    expect(slot.stage).toBe('FAILED');
    expect(slot.failedStep).toBe('pdc');
    expect(slot.stepStatus).toEqual({
      mcu: 'PASSED',
      pdc: 'FAILED',
      ftdi: 'NOT_RUN',
    });
    expect(station1.programFtdiEeprom).not.toHaveBeenCalled();
    expect(dialog.openFailure.calls.mostRecent().args[0]).toBe(
      'Slot 1: PDC Firmware Flashing Failed',
    );
    finish();
  }));

  it('counts a lost console after MCU flashing as an MCU failure', fakeAsync(() => {
    station1.discoverTopologyAndSerial.and.returnValue(
      of(
        new MauiTopologyDiscoveryResponse({
          success: false,
          errorMessage: 'Port lost',
        }),
      ),
    );
    create();
    discovery$.next(bslDevice('/dev/ttyUSB0'));
    tick(MCU_SETTLE_DELAY_MS);

    const slot = component.slots[0];
    expect(slot.failedStep).toBe('mcu');
    expect(slot.stepStatus.mcu).toBe('FAILED');
    expect(pdc.programSinglePdc).not.toHaveBeenCalled();
    finish();
  }));

  it('archives every slot and reloads only after all requests succeeded', fakeAsync(() => {
    deviceMgmt.submitFinalManufacturingResults.and.callFake(req =>
      of(
        new MauiFinalSubmissionResponse({
          success: true,
          message: `Exported to /results/${req.serialNumber}`,
        }),
      ),
    );
    create();
    discovery$.next(bslDevice('/dev/ttyUSB0'));
    discovery$.next(bslDevice('/dev/ttyUSB1'));
    tick(MCU_SETTLE_DELAY_MS);
    tick();
    expect(component.canSaveLogs).toBeTrue();

    component.saveBatchLogsAndFinalize();

    expect(deviceMgmt.saveCardLog).toHaveBeenCalledTimes(2);
    expect(deviceMgmt.submitFinalManufacturingResults).toHaveBeenCalledTimes(2);
    const results =
      deviceMgmt.submitFinalManufacturingResults.calls.argsFor(0)[0];
    expect(results).toEqual(
      jasmine.objectContaining({
        serialNumber: 'MAUI-0',
        operatorId: 'OP_FACTORY',
        stationId: 'STATION_SMT_STEP1',
        mcuFlashed: true,
        pdcFlashed: true,
        ftdiProgrammed: true,
        adbSerial: 'MAUI-0',
        // The programmed FTDI serial, so the backend reports serials_matched.
        ftdiSerial: 'MAUI-0',
        stageStatuses: {
          mcu_bsl_flashing: 'PASSED',
          pdc_fw_flashing: 'PASSED',
          ftdi_eeprom_programming: 'PASSED',
        },
      }),
    );
    expect(component.logsSaved).toBeTrue();
    expect(component.archiveMessages).toEqual([
      'Exported to /results/MAUI-0',
      'Exported to /results/MAUI-1',
    ]);
    fixture.detectChanges();
    const page = (fixture.nativeElement as HTMLElement).textContent || '';
    expect(page).toContain('Exported to /results/MAUI-1');
    expect(pageReload.reload).not.toHaveBeenCalled();
    tick(PAGE_RELOAD_DELAY_MS);
    expect(pageReload.reload).toHaveBeenCalledTimes(1);
    finish();
  }));

  it('reports a failed slot step to MES instead of guessing from the message', fakeAsync(() => {
    station1.programFtdiEeprom.and.returnValue(
      of(new MauiProgramFtdiResponse({success: false, message: 'EEPROM'})),
    );
    create();
    discovery$.next(bslDevice('/dev/ttyUSB0'));
    tick(MCU_SETTLE_DELAY_MS);
    tick();
    component.saveBatchLogsAndFinalize();

    const results =
      deviceMgmt.submitFinalManufacturingResults.calls.mostRecent().args[0];
    expect(results.mcuFlashed).toBeTrue();
    expect(results.pdcFlashed).toBeTrue();
    expect(results.ftdiProgrammed).toBeFalse();
    // Not programmed: the FTDI chip still has its original serial.
    expect(results.ftdiSerial).toBe('FT-0');
    expect(results.stageStatuses).toEqual({
      mcu_bsl_flashing: 'PASSED',
      pdc_fw_flashing: 'PASSED',
      ftdi_eeprom_programming: 'FAILED',
    });
    finish();
  }));

  it('shows a failure, does not reload and retries only failed slots', fakeAsync(() => {
    deviceMgmt.saveCardLog.and.callFake(serial =>
      serial === 'MAUI-1'
        ? throwError(() => new Error('disk full'))
        : of(new MauiSaveLogResponse({success: true})),
    );
    create();
    discovery$.next(bslDevice('/dev/ttyUSB0'));
    discovery$.next(bslDevice('/dev/ttyUSB1'));
    tick(MCU_SETTLE_DELAY_MS);
    tick();
    component.saveBatchLogsAndFinalize();

    expect(component.logsSaved).toBeFalse();
    expect(component.savingLogs).toBeFalse();
    const [title, message, retry] = dialog.openFailure.calls.mostRecent().args;
    expect(title).toBe('Step 1 Log Archival Failed');
    expect(message).toBe('Slot 2: disk full');
    tick(PAGE_RELOAD_DELAY_MS);
    expect(pageReload.reload).not.toHaveBeenCalled();

    deviceMgmt.saveCardLog.calls.reset();
    deviceMgmt.saveCardLog.and.returnValue(
      of(new MauiSaveLogResponse({success: true})),
    );
    retry!();
    expect(deviceMgmt.saveCardLog).toHaveBeenCalledTimes(1);
    expect(deviceMgmt.saveCardLog.calls.mostRecent().args[0]).toBe('MAUI-1');
    expect(component.logsSaved).toBeTrue();
    tick(PAGE_RELOAD_DELAY_MS);
    expect(pageReload.reload).toHaveBeenCalledTimes(1);
    finish();
  }));

  it('refuses to save while boards are still connected', fakeAsync(() => {
    create();
    discovery$.next(bslDevice('/dev/ttyUSB0'));
    tick(MCU_SETTLE_DELAY_MS);
    pdc.discoverConnectedPdcDevices.and.returnValue(
      of(
        new MauiConnectedPdcDevicesResponse({
          devices: [new MauiPdcDeviceInfo({serialNumber: 'MAUI-0'})],
        }),
      ),
    );
    component.saveBatchLogsAndFinalize();

    expect(deviceMgmt.saveCardLog).not.toHaveBeenCalled();
    expect(dialog.openFailure.calls.mostRecent().args[0]).toBe(
      'Units Still Connected (ADB / Hardware Active)',
    );
    finish();
  }));
});
