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
import {of, throwError} from 'rxjs';
import {MauiStep3LabelingComponent} from './maui-step3-labeling';
import {MauiDeviceMgmtService} from '../services/maui-device-mgmt.service';
import {
  COPY_MESSAGE_TIMEOUT_MS,
  STEP3_SERIAL_POLL_INTERVAL_MS,
} from '../maui-common/maui-constants';
import {MauiReadSerialResponse} from '../../proto/maui-manufacturing.pb';

function connected(serial: string): MauiReadSerialResponse {
  return new MauiReadSerialResponse({
    isConnected: true,
    serialNumber: serial,
    adbSerial: serial,
    adbConnected: true,
    ftdiSerial: serial,
    ftdiConnected: true,
    dualEndpointsMatch: true,
    ftdiIsProgrammed: true,
  });
}

describe('MauiStep3LabelingComponent', () => {
  let component: MauiStep3LabelingComponent;
  let fixture: ComponentFixture<MauiStep3LabelingComponent>;
  let deviceMgmt: jasmine.SpyObj<MauiDeviceMgmtService>;

  beforeEach(async () => {
    deviceMgmt = jasmine.createSpyObj<MauiDeviceMgmtService>(
      'MauiDeviceMgmtService',
      ['readConnectedDeviceSerial'],
    );
    deviceMgmt.readConnectedDeviceSerial.and.returnValue(
      of(connected('MAUI-TEST-001')),
    );

    await TestBed.configureTestingModule({
      imports: [MauiStep3LabelingComponent, NoopAnimationsModule],
      providers: [
        provideRouter([]),
        {provide: MauiDeviceMgmtService, useValue: deviceMgmt},
      ],
    }).compileComponents();
  });

  function create(): void {
    fixture = TestBed.createComponent(MauiStep3LabelingComponent);
    component = fixture.componentInstance;
    fixture.detectChanges();
    tick();
  }

  function finish(): void {
    flush();
    discardPeriodicTasks();
  }

  it('detects the electronic serial and renders its barcode', fakeAsync(() => {
    create();
    expect(component.electronicSerial).toBe('MAUI-TEST-001');
    expect(component.deviceConnected).toBeTrue();
    expect(component.svgBarcodeBars.length).toBeGreaterThan(0);
    expect(component.barcodeError).toBe('');
    finish();
  }));

  it('keeps polling after a transient gRPC error', fakeAsync(() => {
    deviceMgmt.readConnectedDeviceSerial.and.returnValue(
      throwError(() => new Error('unavailable')),
    );
    create();
    expect(component.serialDetectionMessage).toBe(
      'Error querying device detection.',
    );
    expect(component.readingSerial).toBeFalse();

    deviceMgmt.readConnectedDeviceSerial.and.returnValue(
      of(connected('MAUI-TEST-002')),
    );
    tick(STEP3_SERIAL_POLL_INTERVAL_MS);
    expect(component.electronicSerial).toBe('MAUI-TEST-002');
    finish();
  }));

  it('refreshes on demand without starting another poller', fakeAsync(() => {
    create();
    expect(deviceMgmt.readConnectedDeviceSerial).toHaveBeenCalledTimes(1);
    component.refreshSerial();
    component.refreshSerial();
    expect(deviceMgmt.readConnectedDeviceSerial).toHaveBeenCalledTimes(3);
    tick(STEP3_SERIAL_POLL_INTERVAL_MS);
    expect(deviceMgmt.readConnectedDeviceSerial).toHaveBeenCalledTimes(4);
    finish();
  }));

  it('does not render a barcode for an unencodable serial', fakeAsync(() => {
    deviceMgmt.readConnectedDeviceSerial.and.returnValue(
      of(connected('MAUI-ÄÖ1')),
    );
    create();
    expect(component.electronicSerial).toBe('MAUI-ÄÖ1');
    expect(component.svgBarcodeBars).toEqual([]);
    expect(component.barcodeError).toContain('"Ä"');
    expect(
      fixture.nativeElement.querySelector('.barcode-error-banner'),
    ).not.toBeNull();
    expect(fixture.nativeElement.querySelector('svg.barcode-svg')).toBeNull();
    finish();
  }));

  it('copies the serial to the clipboard', fakeAsync(() => {
    create();
    spyOn(navigator.clipboard, 'writeText').and.returnValue(Promise.resolve());
    component.copySerialToClipboard();
    tick();
    expect(navigator.clipboard.writeText).toHaveBeenCalledWith('MAUI-TEST-001');
    expect(component.copySuccessMessage).toBe('Serial copied to clipboard!');
    tick(COPY_MESSAGE_TIMEOUT_MS);
    expect(component.copySuccessMessage).toBe('');
    finish();
  }));

  function readoutText(hwRev: 'v1.1' | 'v1.2'): string {
    fixture = TestBed.createComponent(MauiStep3LabelingComponent);
    component = fixture.componentInstance;
    component.hwRev = hwRev;
    fixture.detectChanges();
    tick();
    fixture.detectChanges();
    const text = (fixture.nativeElement as HTMLElement).querySelector(
      '#step3-card-1',
    )!.textContent!;
    fixture.destroy();
    finish();
    return text;
  }

  it('uses the H2H wording on v1.2', fakeAsync(() => {
    const text = readoutText('v1.2');
    expect(text).toContain('H2H Serial:');
    expect(text).toContain('MCU Serial:');
    expect(text).toContain('MCU = H2H serial:');
    expect(text).toContain('Matched');
    expect(text).not.toContain('ADB Serial');
    expect(text).not.toContain('Endpoint Sync');
    expect(text).not.toContain('Dual Endpoints');
  }));

  it('keeps the v1.1 wording', fakeAsync(() => {
    const text = readoutText('v1.1');
    expect(text).toContain('ADB Serial:');
    expect(text).toContain('FTDI Serial:');
    expect(text).toContain('Endpoint Sync:');
    expect(text).toContain('Dual Endpoints Matched');
  }));

  it('lets the Refresh Detection button wrap instead of clipping it', fakeAsync(() => {
    create();
    const button = (fixture.nativeElement as HTMLElement).querySelector(
      '#step3-refresh-btn',
    ) as HTMLElement;
    expect(button.textContent).toContain('Refresh Detection');
    expect(button.classList).toContain('action-btn-wide');
    expect(getComputedStyle(button).whiteSpace).toBe('nowrap');
    expect(getComputedStyle(button.parentElement!).flexWrap).toBe('wrap');
    finish();
  }));
});
