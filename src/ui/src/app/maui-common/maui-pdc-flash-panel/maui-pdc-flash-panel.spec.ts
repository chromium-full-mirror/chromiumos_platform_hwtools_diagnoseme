// Copyright 2026 The ChromiumOS Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

import {ComponentFixture, TestBed} from '@angular/core/testing';
import {NoopAnimationsModule} from '@angular/platform-browser/animations';
import {Subject, of} from 'rxjs';
import {MauiPdcFlashPanelComponent} from './maui-pdc-flash-panel';
import {MauiPdcService} from '../../services/maui-pdc.service';

describe('MauiPdcFlashPanelComponent', () => {
  let component: MauiPdcFlashPanelComponent;
  let fixture: ComponentFixture<MauiPdcFlashPanelComponent>;
  let pdcService: jasmine.SpyObj<MauiPdcService>;

  const devices = [1, 2, 3, 4, 5, 6].map(i => ({
    slotIndex: i,
    serialNumber: `MAUIV12H2601000${i}`,
    devicePort: `/dev/ttyACM${i - 1}`,
    productDescription: 'Maui Diagnostic Controller',
  }));

  beforeEach(async () => {
    pdcService = jasmine.createSpyObj('MauiPdcService', [
      'discoverConnectedPdcDevices',
      'programSinglePdc',
    ]);
    pdcService.discoverConnectedPdcDevices.and.returnValue(
      of({success: true, devices, message: 'Found 6'}) as never,
    );
    await TestBed.configureTestingModule({
      imports: [MauiPdcFlashPanelComponent, NoopAnimationsModule],
      providers: [{provide: MauiPdcService, useValue: pdcService}],
    }).compileComponents();

    fixture = TestBed.createComponent(MauiPdcFlashPanelComponent);
    component = fixture.componentInstance;
    fixture.detectChanges();
  });

  it('stops a pending discovery when destroyed', () => {
    const pending = new Subject<never>();
    pdcService.discoverConnectedPdcDevices.and.returnValue(pending);
    component.discover();
    expect(pending.observed).toBeTrue();
    fixture.destroy();
    expect(pending.observed).toBeFalse();
  });

  it('limits discovered boards to maxSlots', () => {
    expect(component.slots.length).toBe(5);
    expect(component.slots[0].devicePort).toBe('/dev/ttyACM0');
  });

  it('flashes every slot and tracks results', () => {
    pdcService.programSinglePdc.and.callFake(
      (slotIndex: number) =>
        of(
          {progressPercent: 50, isComplete: false},
          slotIndex === 2
            ? {progressPercent: 0, isComplete: true, errorMessage: 'boom'}
            : {progressPercent: 100, isComplete: true, errorMessage: ''},
        ) as never,
    );
    component.flashAll();
    expect(pdcService.programSinglePdc).toHaveBeenCalledTimes(5);
    // The server picks its default PDC firmware.
    expect(pdcService.programSinglePdc.calls.first().args[3]).toBe('');
    expect(component.slots[0].state).toBe('PASSED');
    expect(component.slots[1].state).toBe('FAILED');
    expect(component.anyFlashing).toBeFalse();
  });

  it('does not flash while locked by another flash', () => {
    component.locked = true;
    fixture.detectChanges();
    component.flashAll();
    component.flash(component.slots[0]);
    expect(pdcService.programSinglePdc).not.toHaveBeenCalled();
    const text = fixture.nativeElement.textContent as string;
    expect(text).toContain('PDC flashing is locked');
    const buttons = Array.from(
      fixture.nativeElement.querySelectorAll(
        'button',
      ) as NodeListOf<HTMLButtonElement>,
    ).filter(b => (b.textContent || '').includes('Flash'));
    expect(buttons.length).toBeGreaterThan(0);
    expect(buttons.every(b => b.disabled)).toBeTrue();
  });

  it('reports while it is flashing', () => {
    const progress = new Subject<never>();
    pdcService.programSinglePdc.and.returnValue(progress);
    const busy: boolean[] = [];
    component.busyChange.subscribe(b => busy.push(b));
    component.flash(component.slots[0]);
    component.flash(component.slots[1]);
    expect(busy).toEqual([true]);
    progress.next({progressPercent: 100, isComplete: true} as never);
    expect(busy).toEqual([true, false]);
  });
});
