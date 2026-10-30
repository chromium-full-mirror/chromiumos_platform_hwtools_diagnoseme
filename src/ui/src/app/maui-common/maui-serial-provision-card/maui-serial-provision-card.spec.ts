// Copyright 2026 The ChromiumOS Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

import {ComponentFixture, TestBed} from '@angular/core/testing';
import {NoopAnimationsModule} from '@angular/platform-browser/animations';
import {of, throwError} from 'rxjs';
import {MauiSerialProvisionCardComponent} from './maui-serial-provision-card';
import {MauiMcuService} from '../../services/maui-mcu.service';
import {MauiDialogService} from '../maui-dialog.service';

describe('MauiSerialProvisionCardComponent', () => {
  let component: MauiSerialProvisionCardComponent;
  let fixture: ComponentFixture<MauiSerialProvisionCardComponent>;
  let mcuService: jasmine.SpyObj<MauiMcuService>;
  let dialogService: jasmine.SpyObj<MauiDialogService>;

  beforeEach(async () => {
    mcuService = jasmine.createSpyObj('MauiMcuService', ['provisionMcuSerial']);
    dialogService = jasmine.createSpyObj('MauiDialogService', ['openFailure']);
    await TestBed.configureTestingModule({
      imports: [MauiSerialProvisionCardComponent, NoopAnimationsModule],
      providers: [
        {provide: MauiMcuService, useValue: mcuService},
        {provide: MauiDialogService, useValue: dialogService},
      ],
    }).compileComponents();

    fixture = TestBed.createComponent(MauiSerialProvisionCardComponent);
    component = fixture.componentInstance;
    fixture.detectChanges();
  });

  it('passes and emits the serial on success', () => {
    const emitted: string[] = [];
    component.statusChange.subscribe(e =>
      emitted.push(e.status + ':' + e.serial),
    );
    mcuService.provisionMcuSerial.and.returnValue(
      of({
        success: true,
        alreadyProvisioned: false,
        h2hSerial: 'MAUIV12H26010001',
        previousMcuSerial: 'Uninitialized',
        mcuSerial: 'MAUIV12H26010001',
        devicePort: '/dev/ttyACM0',
        reenumerationS: 3.2,
        message: 'ok',
      }) as never,
    );
    component.run();
    expect(component.status).toBe('PASSED');
    expect(component.done).toBeTrue();
    expect(emitted).toEqual(['PASSED:MAUIV12H26010001']);
    expect(dialogService.openFailure).not.toHaveBeenCalled();
  });

  it('fails and opens the failure dialog', () => {
    mcuService.provisionMcuSerial.and.returnValue(
      of({success: false, message: 'H2H serial invalid'}) as never,
    );
    component.run();
    expect(component.status).toBe('FAILED');
    expect(component.done).toBeFalse();
    expect(dialogService.openFailure).toHaveBeenCalled();
  });

  it('handles gRPC errors', () => {
    mcuService.provisionMcuSerial.and.returnValue(
      throwError(() => new Error('unavailable')),
    );
    component.run();
    expect(component.status).toBe('FAILED');
    expect(component.message).toBe('unavailable');
  });

  it('does not reboot the MCU while other tests are running', () => {
    component.locked = true;
    fixture.detectChanges();
    component.run();
    expect(mcuService.provisionMcuSerial).not.toHaveBeenCalled();
    const buttons: NodeListOf<HTMLButtonElement> =
      fixture.nativeElement.querySelectorAll('button');
    expect(buttons.length).toBeGreaterThan(0);
    buttons.forEach(b => expect(b.disabled).toBeTrue());
  });

  it('can be bypassed', () => {
    const messages: string[] = [];
    component.statusChange.subscribe(e => messages.push(e.message));
    component.bypass();
    expect(component.status).toBe('BYPASSED');
    expect(component.done).toBeTrue();
    expect(messages).toEqual([
      'Serial number provisioning bypassed by operator.',
    ]);
  });

  it('has an unnumbered default title (the pages number it)', () => {
    const text = (fixture.nativeElement as HTMLElement).textContent ?? '';
    expect(text).toContain('Serial Number Provisioning (H2H → MCU)');
    expect(text).not.toContain('0a.');
  });
});
