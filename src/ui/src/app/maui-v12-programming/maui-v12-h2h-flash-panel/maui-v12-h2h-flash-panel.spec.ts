// Copyright 2026 The ChromiumOS Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

import {ComponentFixture, TestBed} from '@angular/core/testing';
import {NoopAnimationsModule} from '@angular/platform-browser/animations';
import {Subject, of, throwError} from 'rxjs';
import {MauiV12H2hFlashPanelComponent} from './maui-v12-h2h-flash-panel';
import {MauiDialogService} from '../../maui-common/maui-dialog.service';
import {MauiMcuService} from '../../services/maui-mcu.service';
import {
  MauiH2hFirmwareListResponse,
  MauiH2hFlashProgress,
} from '../../../proto/maui-manufacturing.pb';

const DIR = '/home/diagnoseme/diagnoseme_h2h_firmware';
const SHA = 'a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4e5f60718293a4b5c6d7e8f90';
const GOOD = {
  fileName: 'h2h_maui_v12.bin',
  sizeBytes: '8704',
  sha256: SHA,
  valid: true,
  error: '',
  warnings: [],
  serialNumber: 'MAUIV12H26090104',
  serialValid: true,
  serialPatchable: true,
  serialPatchError: '',
  vid: 0x18d1,
  pid: 0x506d,
};
const SHORT = {
  ...GOOD,
  fileName: 'short.bin',
  sizeBytes: '100',
  sha256: 'ff'.repeat(32),
  valid: false,
  error: '100 bytes, a PL27A1 H2H image has exactly 8704 bytes',
  serialNumber: '',
  serialValid: false,
  serialPatchable: false,
};
const APP = {
  devicePort: '/dev/ttyACM0',
  serialNumber: 'MAUIV12H26090104',
  stage: 'app',
};

describe('MauiV12H2hFlashPanelComponent', () => {
  let component: MauiV12H2hFlashPanelComponent;
  let fixture: ComponentFixture<MauiV12H2hFlashPanelComponent>;
  let mcuService: jasmine.SpyObj<MauiMcuService>;

  function listResponse(
    files: object[],
    fields: Partial<MauiH2hFirmwareListResponse> = {},
  ): MauiH2hFirmwareListResponse {
    return {
      directory: DIR,
      directoryExists: true,
      files,
      message: `${files.length} image(s) in ${DIR}.`,
      expectedSizeBytes: '8704',
      ...fields,
    } as unknown as MauiH2hFirmwareListResponse;
  }

  function listing(
    files: object[],
    fields: Partial<MauiH2hFirmwareListResponse> = {},
  ): never {
    return of(listResponse(files, fields)) as never;
  }

  function create(consoles: object[] = [APP]): void {
    fixture = TestBed.createComponent(MauiV12H2hFlashPanelComponent);
    component = fixture.componentInstance;
    fixture.componentRef.setInput('consoles', consoles);
    fixture.detectChanges();
  }

  function progress(
    fields: Partial<MauiH2hFlashProgress>,
  ): MauiH2hFlashProgress {
    return fields as MauiH2hFlashProgress;
  }

  function text(): string {
    return (fixture.nativeElement as HTMLElement).textContent || '';
  }

  beforeEach(async () => {
    mcuService = jasmine.createSpyObj('MauiMcuService', [
      'listH2hFirmware',
      'flashH2hFirmware',
      'provisionMcuSerial',
    ]);
    mcuService.listH2hFirmware.and.returnValue(listing([SHORT, GOOD]));
    await TestBed.configureTestingModule({
      imports: [MauiV12H2hFlashPanelComponent, NoopAnimationsModule],
      providers: [
        {provide: MauiMcuService, useValue: mcuService},
        {
          provide: MauiDialogService,
          useValue: jasmine.createSpyObj('MauiDialogService', ['openFailure']),
        },
      ],
    }).compileComponents();
  });

  it('shows the directory and the images, picks the only valid one', () => {
    create();
    const el: HTMLElement = fixture.nativeElement;
    expect(el.querySelector('#h2h-dir')?.textContent).toContain(DIR);
    const rows = el.querySelectorAll('#h2h-files tbody tr');
    expect(rows.length).toBe(2);
    expect(rows[0].textContent).toContain('exactly 8704 bytes');
    expect(rows[1].textContent).toContain('h2h_maui_v12.bin');
    expect(rows[1].textContent).toContain('8704 B');
    expect(rows[1].textContent).toContain('a1b2c3d4e5f6');
    expect(rows[1].textContent).not.toContain('a1b2c3d4e5f60');
    expect(rows[1].textContent).toContain('MAUIV12H26090104');
    expect(component.selectedFile).toBe('h2h_maui_v12.bin');
    expect(component.selectedPort).toBe('/dev/ttyACM0');
    expect(component.canStart).toBeTrue();
    expect(mcuService.listH2hFirmware).toHaveBeenCalledWith('/dev/ttyACM0');
    const steps = el.querySelectorAll('#h2h-steps li');
    expect(steps.length).toBe(5);
    expect(steps[0].textContent).toContain(DIR);
    expect(steps[0].textContent).toContain('The station creates this folder');
    expect(steps[0].textContent).toContain(
      'prolific_raw_<number>_<serial>.bin',
    );
    expect(steps[4].textContent).toContain('run the H2H SPI check again');
    expect(el.querySelector('#h2h-fw-status')).toBeNull();
  });

  it('warns before flashing when the MCU App is older than stable', () => {
    mcuService.listH2hFirmware.and.returnValue(
      listing([GOOD], {
        mcuVersion: '0.64-86c6fbb',
        stableVersion: '0.69-5324f43',
        firmwareVerdict: 'older',
        firmwareWarning: true,
        firmwareMessage:
          'MCU App 0.64-86c6fbb is older than the stable 0.69-5324f43; if ' +
          'flashing fails, update the MCU App first.',
      }),
    );
    create();
    const box = fixture.nativeElement.querySelector('#h2h-fw-status');
    expect(box.classList).toContain('fw-warning');
    expect(box.textContent).toContain('WARNING:');
    expect(box.textContent).toContain('older than the stable 0.69-5324f43');
    expect(box.textContent).toContain('MCU App update over USB');
    // A warning only: flashing stays possible.
    expect(component.canStart).toBeTrue();
  });

  it('shows a newer MCU App as information only', () => {
    mcuService.listH2hFirmware.and.returnValue(
      listing([GOOD], {
        firmwareVerdict: 'newer',
        firmwareWarning: false,
        firmwareMessage:
          'MCU App 0.71-abcdef1 is newer than the stable 0.69-5324f43.',
      }),
    );
    create();
    const box = fixture.nativeElement.querySelector('#h2h-fw-status');
    expect(box.classList).toContain('fw-info');
    expect(box.textContent).not.toContain('WARNING');
  });

  it('checks the MCU App again when another console is selected', async () => {
    create();
    mcuService.listH2hFirmware.calls.reset();
    fixture.componentRef.setInput('consoles', [
      {...APP, devicePort: '/dev/ttyACM2'},
    ]);
    fixture.detectChanges();
    await Promise.resolve();
    expect(component.selectedPort).toBe('/dev/ttyACM2');
    expect(mcuService.listH2hFirmware).toHaveBeenCalledWith('/dev/ttyACM2');
  });

  it('asks for a choice when several images are valid', () => {
    mcuService.listH2hFirmware.and.returnValue(
      listing([GOOD, {...GOOD, fileName: 'other.bin'}]),
    );
    create();
    expect(component.selectedFile).toBe('');
    expect(component.canStart).toBeFalse();
    expect(component.blockReason).toBe('Select the H2H image to flash.');
    component.selectedFile = 'other.bin';
    expect(component.canStart).toBeTrue();
  });

  it('explains a missing or empty directory', () => {
    mcuService.listH2hFirmware.and.returnValue(
      listing([], {
        directoryExists: false,
        message: `The H2H firmware directory ${DIR} does not exist on this station.`,
      }),
    );
    create();
    expect(text()).toContain('does not exist on this station');
    expect(component.canStart).toBeFalse();
    expect(component.blockReason).toBe('No valid H2H image to flash.');
    mcuService.listH2hFirmware.and.returnValue(
      throwError(() => new Error('x')),
    );
    component.refresh();
    expect(component.listMessage).toBe('H2H image listing failed: x');
  });

  it('validates the new serial number', () => {
    create();
    component.changeSerial = true;
    component.newSerial = 'MAUIV12H123';
    fixture.detectChanges();
    const el: HTMLElement = fixture.nativeElement;
    expect(el.querySelector('#h2h-serial-error')?.textContent).toContain(
      'MAUIV12H followed by 8 digits',
    );
    expect(component.canStart).toBeFalse();
    component.newSerial = ' mauiv12h26090199 ';
    fixture.detectChanges();
    expect(el.querySelector('#h2h-serial-error')).toBeNull();
    expect(component.serialToWrite).toBe('MAUIV12H26090199');
    expect(text()).toContain('the file itself is not modified');
    expect(component.canStart).toBeTrue();
  });

  it('refuses images whose serial cannot be changed or is invalid', () => {
    mcuService.listH2hFirmware.and.returnValue(
      listing([
        {
          ...GOOD,
          serialNumber: 'TESTV11111000001',
          serialValid: false,
          serialPatchable: false,
          serialPatchError: 'the serial descriptor is at 0x36',
        },
      ]),
    );
    create();
    expect(component.blockReason).toContain("use 'Change serial number'");
    component.changeSerial = true;
    component.newSerial = 'MAUIV12H26090199';
    expect(component.serialError).toContain('cannot be changed safely');
    expect(component.canStart).toBeFalse();
  });

  it('needs an MCU App console', () => {
    create([{...APP, stage: 'rw_bootloader'}]);
    expect(component.selectedPort).toBe('');
    expect(component.blockReason).toContain('No Maui MCU App console');
  });

  it('flashes and streams the progress', () => {
    const stream = new Subject<MauiH2hFlashProgress>();
    mcuService.flashH2hFirmware.and.returnValue(stream as never);
    create();
    const busy: boolean[] = [];
    component.busyChange.subscribe(b => busy.push(b));
    component.changeSerial = true;
    component.newSerial = 'MAUIV12H26090199';
    component.readBack = false;
    component.start();
    expect(mcuService.flashH2hFirmware).toHaveBeenCalledWith(
      '/dev/ttyACM0',
      'h2h_maui_v12.bin',
      SHA,
      'MAUIV12H26090199',
      false,
    );
    expect(component.canStart).toBeFalse();
    stream.next(
      progress({
        phase: 'erasing',
        phasePercent: 50,
        progressPercent: 30,
        statusMessage: 'Erasing... 50%',
        logLine: 'INFO:   erase  50% (262144/524288 B)',
      }),
    );
    expect(component.phaseLabel).toBe('Erasing 50%');
    expect(component.progress).toBe(30);
    stream.next(
      progress({
        isComplete: true,
        success: true,
        phase: 'done',
        progressPercent: 100,
        statusMessage: 'H2H firmware h2h_maui_v12.bin flashed.',
        hostSerial: 'MAUIV12H26090199',
        mcuSerialMismatch: true,
      }),
    );
    stream.complete();
    expect(component.result).toBe('PASSED');
    expect(component.showSerialCard).toBeTrue();
    expect(busy).toEqual([true, false]);
    expect(component.log).toEqual(['INFO:   erase  50% (262144/524288 B)']);
  });

  it('shows the failure of a flash', () => {
    mcuService.flashH2hFirmware.and.returnValue(
      of(
        progress({
          isComplete: true,
          success: false,
          phase: 'failed',
          statusMessage: 'x',
          errorMessage: 'MCU App 0.64-86c6fbb is not an approved build.',
        }),
      ) as never,
    );
    create();
    component.start();
    expect(component.result).toBe('FAILED');
    expect(component.status).toContain('not an approved build');
    expect(component.showSerialCard).toBeFalse();
    mcuService.flashH2hFirmware.and.returnValue(
      throwError(() => new Error('stream reset')),
    );
    component.start();
    expect(component.status).toBe('H2H flash error: stream reset');
  });

  it('does not start while the page locks it', () => {
    create();
    fixture.componentRef.setInput('locked', true);
    fixture.detectChanges();
    expect(component.canStart).toBeFalse();
    component.start();
    expect(mcuService.flashH2hFirmware).not.toHaveBeenCalled();
    expect(text()).toContain('Wait until the other MCU programming step');
  });

  it('cancels a running flash when the page is left', () => {
    const stream = new Subject<MauiH2hFlashProgress>();
    mcuService.flashH2hFirmware.and.returnValue(stream as never);
    create();
    component.start();
    expect(stream.observed).toBeTrue();
    fixture.destroy();
    expect(stream.observed).toBeFalse();
  });

  it('redraws itself when the RPC replies arrive (zoneless)', () => {
    const list = new Subject<MauiH2hFirmwareListResponse>();
    const stream = new Subject<MauiH2hFlashProgress>();
    mcuService.listH2hFirmware.and.returnValue(list as never);
    mcuService.flashH2hFirmware.and.returnValue(stream as never);
    create();
    const el: HTMLElement = fixture.nativeElement;
    expect(el.querySelector('#h2h-files')).toBeNull();
    expect(el.querySelector('#h2h-fw-status')).toBeNull();

    // No fixture.detectChanges() from here on: like in the browser, nothing
    // but the component itself redraws the page after an RPC reply.
    list.next(
      listResponse([GOOD], {
        firmwareVerdict: 'unknown',
        firmwareWarning: true,
        firmwareMessage:
          'MCU App 0.69-1234567 is another build than the stable ' +
          '0.69-5324f43; if flashing fails, update the MCU App first.',
      }),
    );
    expect(el.querySelector('#h2h-fw-status')?.textContent).toContain(
      'WARNING:',
    );
    expect(el.querySelector('#h2h-fw-status')?.textContent).toContain(
      'another build than the stable',
    );
    expect(el.querySelector('#h2h-dir')?.textContent).toContain(DIR);
    expect(el.querySelectorAll('#h2h-files tbody tr').length).toBe(1);
    expect(
      (el.querySelector('#h2h-start') as HTMLButtonElement).disabled,
    ).toBeFalse();

    (el.querySelector('#h2h-start') as HTMLButtonElement).click();
    stream.next(
      progress({
        phase: 'programming',
        phasePercent: 40,
        progressPercent: 60,
        statusMessage: 'Programming... 40%',
      }),
    );
    expect(el.querySelector('#h2h-status')?.textContent).toContain(
      'Programming... 40%',
    );
    stream.next(
      progress({
        isComplete: true,
        success: true,
        phase: 'done',
        progressPercent: 100,
        statusMessage: 'H2H firmware flashed; iSerial MAUIV12H26090104.',
      }),
    );
    expect(el.querySelector('#h2h-status')?.textContent).toContain(
      'iSerial MAUIV12H26090104',
    );
    expect(el.querySelector('#h2h-status')?.classList).toContain('passed');
  });
});
