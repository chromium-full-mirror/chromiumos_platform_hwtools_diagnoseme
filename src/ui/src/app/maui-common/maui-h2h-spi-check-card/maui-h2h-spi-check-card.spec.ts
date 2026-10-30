// Copyright 2026 The ChromiumOS Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

import {ComponentFixture, TestBed} from '@angular/core/testing';
import {NoopAnimationsModule} from '@angular/platform-browser/animations';
import {Subject, of, throwError} from 'rxjs';
import {MauiH2hSpiCheckCardComponent} from './maui-h2h-spi-check-card';
import {MauiMcuService} from '../../services/maui-mcu.service';
import {MauiDialogService} from '../maui-dialog.service';
import {MauiH2hSpiCheckResponse} from '../../../proto/maui-manufacturing.pb';

describe('MauiH2hSpiCheckCardComponent', () => {
  let component: MauiH2hSpiCheckCardComponent;
  let fixture: ComponentFixture<MauiH2hSpiCheckCardComponent>;
  let mcuService: jasmine.SpyObj<MauiMcuService>;
  let dialogService: jasmine.SpyObj<MauiDialogService>;

  const SN = 'MAUIV12H26090104';
  const passed = {
    success: true,
    firmwareSupported: true,
    hostSerialBefore: SN,
    spiSerial: SN,
    hostSerialAfter: SN,
    jedecId: '0x852013',
    flashSize: '512 KB',
    message: 'H2H SPI flash serial matches',
  };

  function reply(res: Partial<MauiH2hSpiCheckResponse>): void {
    mcuService.checkH2hSpiSerial.and.returnValue(of(res) as never);
  }

  function text(): string {
    return (fixture.nativeElement as HTMLElement).textContent ?? '';
  }

  beforeEach(async () => {
    mcuService = jasmine.createSpyObj('MauiMcuService', ['checkH2hSpiSerial']);
    dialogService = jasmine.createSpyObj('MauiDialogService', ['openFailure']);
    await TestBed.configureTestingModule({
      imports: [MauiH2hSpiCheckCardComponent, NoopAnimationsModule],
      providers: [
        {provide: MauiMcuService, useValue: mcuService},
        {provide: MauiDialogService, useValue: dialogService},
      ],
    }).compileComponents();

    fixture = TestBed.createComponent(MauiH2hSpiCheckCardComponent);
    component = fixture.componentInstance;
    fixture.detectChanges();
  });

  it('has the v1.2 card title', () => {
    expect(text()).toContain('H2H Flash Access Check (SPI)');
  });

  it('passes and emits the SPI serial', () => {
    const emitted: string[] = [];
    const running: boolean[] = [];
    component.statusChange.subscribe(e =>
      emitted.push(e.status + ':' + e.serial + ':' + e.message),
    );
    component.runningChange.subscribe(r => running.push(r));
    component.serialNumber = SN;
    component.devicePort = '/dev/ttyACM0';
    reply(passed);
    component.run();
    fixture.detectChanges();
    expect(mcuService.checkH2hSpiSerial).toHaveBeenCalledWith(
      SN,
      '/dev/ttyACM0',
    );
    expect(component.status).toBe('PASSED');
    expect(component.done).toBeTrue();
    expect(emitted).toEqual(['PASSED:' + SN + ':H2H SPI flash serial matches']);
    expect(running).toEqual([true, false]);
    expect(dialogService.openFailure).not.toHaveBeenCalled();
    expect(text()).toContain('JEDEC ID 0x852013, 512 KB');
    expect(text()).toContain(SN);
  });

  it('asks for an MCU App update on unsupported firmware', () => {
    reply({
      success: false,
      firmwareSupported: false,
      hostSerialBefore: SN,
      message:
        'MCU App 0.64-86c6fbb is not an approved build (approved: 0.69-5324f43). ' +
        'The H2H SPI check needs an approved MCU App: update the MCU App.',
    });
    component.run();
    fixture.detectChanges();
    expect(component.status).toBe('FAILED');
    expect(component.done).toBeFalse();
    expect(dialogService.openFailure.calls.mostRecent().args[0]).toBe(
      'MCU App Update Required',
    );
    expect(text()).toContain('Not supported: update the MCU App');
    // The status badges are color coded (shared maui-status-badges styles).
    const badge = (fixture.nativeElement as HTMLElement).querySelector(
      '.badge.badge-fail',
    )!;
    expect(getComputedStyle(badge).color).toBe('rgb(197, 34, 31)');
  });

  it('shows a serial mismatch and offers retry and bypass', () => {
    reply({
      ...passed,
      success: false,
      spiSerial: 'MAUIV12H26090100',
      message: 'SPI serial does not match',
    });
    component.run();
    fixture.detectChanges();
    expect(component.status).toBe('FAILED');
    const el: HTMLElement = fixture.nativeElement;
    expect(el.querySelectorAll('.mismatch').length).toBe(2);
    const [title, , retry, bypass] =
      dialogService.openFailure.calls.mostRecent().args;
    expect(title).toBe('H2H Flash Access Check Failed');
    reply(passed);
    (retry as () => void)();
    expect(component.status).toBe('PASSED');
    (bypass as () => void)();
    expect(component.status).toBe('BYPASSED');
    expect(component.done).toBeTrue();
  });

  it('handles gRPC errors', () => {
    const running: boolean[] = [];
    component.runningChange.subscribe(r => running.push(r));
    mcuService.checkH2hSpiSerial.and.returnValue(
      throwError(() => new Error('unavailable')),
    );
    component.run();
    expect(component.status).toBe('FAILED');
    expect(component.message).toBe('unavailable');
    expect(running).toEqual([true, false]);
  });

  it('runs only once at a time', () => {
    const pending = new Subject<MauiH2hSpiCheckResponse>();
    mcuService.checkH2hSpiSerial.and.returnValue(pending as never);
    component.run();
    component.run();
    expect(mcuService.checkH2hSpiSerial).toHaveBeenCalledTimes(1);
    expect(component.inProgress).toBeTrue();
  });

  it('is locked while other tests run', () => {
    component.locked = true;
    fixture.detectChanges();
    component.run();
    expect(mcuService.checkH2hSpiSerial).not.toHaveBeenCalled();
    const buttons: NodeListOf<HTMLButtonElement> =
      fixture.nativeElement.querySelectorAll('button');
    expect(buttons.length).toBe(2);
    buttons.forEach(b => expect(b.disabled).toBeTrue());
  });

  it('can be bypassed', () => {
    const emitted: string[] = [];
    component.statusChange.subscribe(e => emitted.push(e.status));
    component.bypass();
    expect(component.status).toBe('BYPASSED');
    expect(component.done).toBeTrue();
    expect(emitted).toEqual(['BYPASSED']);
  });
});
