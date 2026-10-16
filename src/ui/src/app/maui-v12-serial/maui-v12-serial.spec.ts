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
import {NoopAnimationsModule} from '@angular/platform-browser/animations';
import {provideRouter} from '@angular/router';
import {of, throwError} from 'rxjs';
import {
  MauiV12SerialComponent,
  SERIAL_LABEL_LOG,
  SERIAL_LABEL_STATION_ID,
  UNIT_STATUS_POLL_MS,
} from './maui-v12-serial';
import {MauiStep3LabelingComponent} from '../maui-step3-labeling/maui-step3-labeling';
import {MauiDeviceMgmtService} from '../services/maui-device-mgmt.service';
import {MauiMcuService} from '../services/maui-mcu.service';

describe('MauiV12SerialComponent', () => {
  let fixture: ComponentFixture<MauiV12SerialComponent>;
  let component: MauiV12SerialComponent;
  let mockMcu: {getUnitStatus: jasmine.Spy};

  const status = {
    mcuDetected: true,
    mcu: {
      devicePort: '/dev/ttyACM0',
      serialNumber: 'MAUIV12H26010001',
      stage: 'app',
    },
    mcuVersion: '0.59.1',
    hwrev: 2,
    mcuNvsSerial: 'MAUIV12H26010001',
    h2hDetected: true,
    h2hSerial: 'MAUIV12H26010001',
    h2hSerialValid: true,
    serialsMatch: true,
    hubUsb2Detected: true,
    hubUsb3Detected: true,
    message: '',
  };

  let mockDeviceMgmt: {[name: string]: jasmine.Spy};

  function verifyResponse(matches: boolean) {
    return {
      matches,
      scannedBarcode: matches ? 'MAUIV12H26010001' : 'MAUIV12H26019999',
      adbSerial: 'MAUIV12H26010001',
      ftdiSerial: 'MAUIV12H26010001',
      adbMatches: matches,
      ftdiMatches: matches,
      message: matches ? 'Verification PASSED' : 'Sticker Mismatch',
    };
  }

  function setup(): void {
    mockDeviceMgmt = {
      readConnectedDeviceSerial: jasmine
        .createSpy()
        .and.returnValue(
          of({isConnected: true, serialNumber: 'MAUIV12H26010001'}),
        ),
      verifyScannedLabel: jasmine
        .createSpy()
        .and.returnValue(of(verifyResponse(true))),
      saveCardLog: jasmine.createSpy().and.returnValue(of({success: true})),
      submitFinalManufacturingResults: jasmine
        .createSpy()
        .and.returnValue(of({success: true, message: 'Results recorded'})),
    };
    TestBed.configureTestingModule({
      imports: [MauiV12SerialComponent, NoopAnimationsModule],
      providers: [
        provideRouter([]),
        {provide: MauiDeviceMgmtService, useValue: mockDeviceMgmt},
        {provide: MauiMcuService, useValue: mockMcu},
      ],
    });
    fixture = TestBed.createComponent(MauiV12SerialComponent);
    component = fixture.componentInstance;
  }

  it('shows the enumeration status inside the labeling page', fakeAsync(() => {
    mockMcu = {getUnitStatus: jasmine.createSpy().and.returnValue(of(status))};
    setup();
    fixture.detectChanges();
    tick();
    fixture.detectChanges();

    const labeling = fixture.debugElement.query(
      d => d.componentInstance instanceof MauiStep3LabelingComponent,
    ).componentInstance as MauiStep3LabelingComponent;
    expect(labeling.hwRev).toBe('v1.2');

    const el: HTMLElement = fixture.nativeElement;
    expect(el.querySelector('#v12-status-card')).toBeTruthy();
    expect(el.querySelector('#v12-mcu-serial')?.textContent).toContain(
      'MAUIV12H26010001',
    );
    expect(el.querySelector('#v12-serials-match')?.textContent).toContain(
      'Yes',
    );

    tick(UNIT_STATUS_POLL_MS);
    expect(mockMcu.getUnitStatus).toHaveBeenCalledTimes(2);
    fixture.destroy();
    discardPeriodicTasks();
  }));

  it('reports a status read error and keeps polling', fakeAsync(() => {
    mockMcu = {
      getUnitStatus: jasmine.createSpy().and.returnValues(
        throwError(() => new Error('boom')),
        of(status),
      ),
    };
    setup();
    fixture.detectChanges();
    tick();
    fixture.detectChanges();
    expect(component.error).toContain('boom');
    expect(
      fixture.nativeElement.querySelector('#v12-status-error'),
    ).toBeTruthy();

    tick(UNIT_STATUS_POLL_MS);
    fixture.detectChanges();
    expect(component.error).toBe('');
    expect(component.status?.serialsMatch).toBeTrue();
    fixture.destroy();
    discardPeriodicTasks();
  }));

  /** Types |text| into the barcode field like a scanner and hits Enter. */
  function scan(text: string): void {
    const input: HTMLInputElement =
      fixture.nativeElement.querySelector('#v12-barcode-input');
    for (const key of text) {
      input.dispatchEvent(new KeyboardEvent('keydown', {key}));
      input.value += key;
      input.dispatchEvent(new Event('input'));
    }
    input.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter'}));
  }

  function startWithUnit(): void {
    mockMcu = {getUnitStatus: jasmine.createSpy().and.returnValue(of(status))};
    setup();
    fixture.detectChanges();
    tick();
    fixture.detectChanges();
  }

  it('checks a scanned barcode and submits the logs', fakeAsync(() => {
    startWithUnit();
    scan('MAUIV12H26010001');
    tick();
    fixture.detectChanges();

    expect(mockDeviceMgmt['verifyScannedLabel']).toHaveBeenCalledWith(
      'MAUIV12H26010001',
      'MAUIV12H26010001',
      'MAUIV12H26010001',
      'MAUIV12H26010001',
    );
    const [serial, logName, content] =
      mockDeviceMgmt['saveCardLog'].calls.mostRecent().args;
    expect(serial).toBe('MAUIV12H26010001');
    expect(logName).toBe(SERIAL_LABEL_LOG);
    expect(content).toContain('ALL_PASSED=true');
    expect(content).toContain('MCU version: 0.59.1');
    expect(content).toContain('H2H serial: MAUIV12H26010001');
    expect(content).toContain('Barcode matches H2H and MCU: PASS');
    const submitArgs =
      mockDeviceMgmt['submitFinalManufacturingResults'].calls.mostRecent().args;
    expect(submitArgs[2]).toBe(SERIAL_LABEL_STATION_ID);
    expect(submitArgs[8]).toBeTrue();
    expect(component.submitted).toBeTrue();
    expect(component.overallPassed).toBeTrue();
    expect(
      fixture.nativeElement.querySelector('#v12-submit-done').textContent,
    ).toContain('PASS logs submitted');
    fixture.destroy();
    discardPeriodicTasks();
  }));

  it('lets the operator submit a failed barcode check', fakeAsync(() => {
    startWithUnit();
    mockDeviceMgmt['verifyScannedLabel'].and.returnValue(
      of(verifyResponse(false)),
    );
    scan('MAUIV12H26019999');
    tick();
    fixture.detectChanges();
    expect(mockDeviceMgmt['saveCardLog']).not.toHaveBeenCalled();

    fixture.nativeElement.querySelector('#v12-submit-logs').click();
    tick();
    const content = mockDeviceMgmt['saveCardLog'].calls.mostRecent().args[2];
    expect(content).toContain('ALL_PASSED=false');
    expect(content).toContain('Barcode matches H2H and MCU: FAIL');
    expect(
      mockDeviceMgmt['submitFinalManufacturingResults'].calls.mostRecent()
        .args[8],
    ).toBeFalse();
    fixture.destroy();
    discardPeriodicTasks();
  }));

  it('blocks pasting into the barcode field', fakeAsync(() => {
    startWithUnit();
    const input: HTMLInputElement =
      fixture.nativeElement.querySelector('#v12-barcode-input');
    const paste = new Event('paste', {cancelable: true});
    input.dispatchEvent(paste);
    expect(paste.defaultPrevented).toBeTrue();

    const ctrlV = new KeyboardEvent('keydown', {
      key: 'v',
      ctrlKey: true,
      cancelable: true,
    });
    input.dispatchEvent(ctrlV);
    expect(ctrlV.defaultPrevented).toBeTrue();

    const shiftInsert = new KeyboardEvent('keydown', {
      key: 'Insert',
      shiftKey: true,
      cancelable: true,
    });
    input.dispatchEvent(shiftInsert);
    expect(shiftInsert.defaultPrevented).toBeTrue();
    fixture.detectChanges();
    expect(
      fixture.nativeElement.querySelector('#v12-barcode-warning'),
    ).toBeTruthy();
    fixture.destroy();
    discardPeriodicTasks();
  }));

  it('rejects text that was not typed', fakeAsync(() => {
    startWithUnit();
    // Value set without key presses, e.g. by a paste path we did not catch.
    component.scannedBarcode = 'MAUIV12H26010001';
    component.verifyBarcode();
    expect(mockDeviceMgmt['verifyScannedLabel']).not.toHaveBeenCalled();
    expect(component.inputWarning).toContain('not pasted');
    expect(component.scannedBarcode).toBe('');
    fixture.destroy();
    discardPeriodicTasks();
  }));

  it('starts over when another unit is connected', fakeAsync(() => {
    const next = {
      ...status,
      mcu: {...status.mcu, serialNumber: 'MAUIV12H26010002'},
      mcuNvsSerial: 'MAUIV12H26010002',
      h2hSerial: 'MAUIV12H26010002',
    };
    mockMcu = {
      getUnitStatus: jasmine.createSpy().and.returnValues(of(status), of(next)),
    };
    setup();
    fixture.detectChanges();
    tick();
    fixture.detectChanges();
    scan('MAUIV12H26010001');
    tick();
    expect(component.submitted).toBeTrue();

    tick(UNIT_STATUS_POLL_MS);
    expect(component.barcodeResult).toBeNull();
    expect(component.submitted).toBeFalse();
    expect(component.canScan).toBeTrue();
    fixture.destroy();
    discardPeriodicTasks();
  }));

  const noUnit = {
    mcuDetected: false,
    h2hDetected: false,
    h2hSerial: '',
    message: 'No unit',
  };

  it('arms the barcode check when a unit is connected', fakeAsync(() => {
    mockMcu = {
      getUnitStatus: jasmine
        .createSpy()
        .and.returnValues(of(noUnit), of(status), of(status)),
    };
    setup();
    fixture.detectChanges();
    tick();
    fixture.detectChanges();
    expect(component.canScan).toBeFalse();

    tick(UNIT_STATUS_POLL_MS);
    fixture.detectChanges();
    expect(component.canScan).toBeTrue();
    scan('MAUIV12H26010001');
    tick();
    expect(mockDeviceMgmt['verifyScannedLabel']).toHaveBeenCalled();
    fixture.destroy();
    discardPeriodicTasks();
  }));

  it('shows console fields as pending while they are read', fakeAsync(() => {
    mockMcu = {
      getUnitStatus: jasmine
        .createSpy()
        .and.returnValue(of({...status, mcuVersion: '', consolePending: true})),
    };
    setup();
    fixture.detectChanges();
    tick();
    fixture.detectChanges();
    expect(component.consoleText('')).toBe('reading...');
    expect(component.canScan).toBeTrue();
    fixture.destroy();
    discardPeriodicTasks();
  }));

  it('submits a pending result when the unit is unplugged', fakeAsync(() => {
    mockMcu = {
      getUnitStatus: jasmine
        .createSpy()
        .and.returnValues(of(status), of(noUnit), of(noUnit)),
    };
    setup();
    fixture.detectChanges();
    tick();
    fixture.detectChanges();
    mockDeviceMgmt['verifyScannedLabel'].and.returnValue(
      of(verifyResponse(false)),
    );
    scan('MAUIV12H26019999');
    tick();
    expect(mockDeviceMgmt['saveCardLog']).not.toHaveBeenCalled();

    tick(UNIT_STATUS_POLL_MS);
    const [serial, , content] =
      mockDeviceMgmt['saveCardLog'].calls.mostRecent().args;
    expect(serial).toBe('MAUIV12H26010001');
    expect(content).toContain('ALL_PASSED=false');
    expect(component.submitted).toBeTrue();
    fixture.destroy();
    discardPeriodicTasks();
  }));
});
