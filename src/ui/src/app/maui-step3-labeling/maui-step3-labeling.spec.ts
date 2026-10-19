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
import {of} from 'rxjs';
import {MauiStep3LabelingComponent} from './maui-step3-labeling';
import {MauiDeviceMgmtService} from '../services/maui-device-mgmt.service';

describe('MauiStep3LabelingComponent', () => {
  let component: MauiStep3LabelingComponent;
  let fixture: ComponentFixture<MauiStep3LabelingComponent>;

  const mockDeviceMgmtService = {
    readConnectedDeviceSerial: jasmine.createSpy().and.returnValue(
      of({
        isConnected: true,
        serialNumber: 'MAUI-TEST-001',
        adbSerial: 'MAUI-TEST-001',
        adbConnected: true,
        ftdiSerial: 'MAUI-TEST-001',
        ftdiConnected: true,
        dualEndpointsMatch: true,
        ftdiIsProgrammed: true,
      }),
    ),
  };

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [MauiStep3LabelingComponent, NoopAnimationsModule],
      providers: [
        provideRouter([]),
        {provide: MauiDeviceMgmtService, useValue: mockDeviceMgmtService},
      ],
    }).compileComponents();

    fixture = TestBed.createComponent(MauiStep3LabelingComponent);
    component = fixture.componentInstance;
    fixture.detectChanges();
  });

  it('should create and detect electronic serial and generate SVG barcode bars', fakeAsync(() => {
    expect(component).toBeTruthy();
    // The serial is polled with timer(0, 2000): restart the loop inside the
    // fake zone and let the first poll run.
    component.pollElectronicSerial();
    tick();
    fixture.detectChanges();
    expect(component.electronicSerial).toBe('MAUI-TEST-001');
    expect(component.deviceConnected).toBeTrue();
    expect(component.svgBarcodeBars.length).toBeGreaterThan(0);
    fixture.destroy();
    discardPeriodicTasks();
  }));

  it('should copy serial to clipboard', () => {
    spyOn(navigator.clipboard, 'writeText').and.returnValue(Promise.resolve());
    component.electronicSerial = 'MAUI-TEST-001';
    component.copySerialToClipboard();
    expect(navigator.clipboard.writeText).toHaveBeenCalledWith('MAUI-TEST-001');
  });

  it('restarts instead of stacking the polling loop on refresh', fakeAsync(() => {
    const spy = mockDeviceMgmtService.readConnectedDeviceSerial;
    component.pollElectronicSerial();
    component.pollElectronicSerial();
    spy.calls.reset();
    tick(2000);
    // One loop polls at t=0 and t=2000; stacked loops would poll 4 times.
    expect(spy).toHaveBeenCalledTimes(2);
    fixture.destroy();
    discardPeriodicTasks();
  }));

  function readoutText(hwRev: 'v1.1' | 'v1.2'): string {
    fixture.destroy();
    fixture = TestBed.createComponent(MauiStep3LabelingComponent);
    component = fixture.componentInstance;
    component.hwRev = hwRev;
    fixture.detectChanges();
    component.pollElectronicSerial();
    tick();
    fixture.detectChanges();
    const text = (fixture.nativeElement as HTMLElement).querySelector(
      '#step3-card-1',
    )!.textContent!;
    fixture.destroy();
    discardPeriodicTasks();
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

  it('lets the Refresh Detection button wrap instead of clipping it', () => {
    const button = (fixture.nativeElement as HTMLElement).querySelector(
      '#step3-refresh-btn',
    ) as HTMLElement;
    expect(button.textContent).toContain('Refresh Detection');
    expect(button.classList).toContain('action-btn-wide');
    expect(getComputedStyle(button).whiteSpace).toBe('nowrap');
    expect(getComputedStyle(button.parentElement!).flexWrap).toBe('wrap');
  });

  it('should generate valid Code 128 barcode bars for any serial', () => {
    component.generateBarcode('ABC123456');
    expect(component.svgBarcodeBars.length).toBeGreaterThan(0);
    expect(component.barcodeTotalWidth).toBeGreaterThan(100);
  });
});
