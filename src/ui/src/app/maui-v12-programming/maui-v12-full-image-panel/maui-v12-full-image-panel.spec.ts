// Copyright 2026 The ChromiumOS Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

import {ComponentFixture, TestBed} from '@angular/core/testing';
import {NoopAnimationsModule} from '@angular/platform-browser/animations';
import {Subject, of, throwError} from 'rxjs';
import {
  LAST_UART_PORT_KEY,
  MauiV12FullImagePanelComponent,
} from './maui-v12-full-image-panel';
import {MauiDialogService} from '../../maui-common/maui-dialog.service';
import {MauiMcuService} from '../../services/maui-mcu.service';
import {MauiMcuFullImageProgress} from '../../../proto/maui-manufacturing.pb';

const FTDI = {
  devicePort: '/dev/ttyUSB0',
  byId: '/dev/serial/by-id/usb-FTDI_TTL232R-3V3_FT9XYZ-if00-port0',
  driver: 'ftdi_sio',
  vid: 0x0403,
  pid: 0x6001,
  label: 'usb-FTDI_TTL232R-3V3_FT9XYZ-if00-port0 (/dev/ttyUSB0, ...)',
};
const CP2102 = {
  devicePort: '/dev/ttyUSB1',
  byId: '',
  driver: 'cp210x',
  vid: 0x10c4,
  pid: 0xea60,
  manufacturer: 'Silicon Labs',
  product: 'CP2102N',
  label: 'Silicon Labs CP2102N (/dev/ttyUSB1, ...)',
};
const MIB = String(1024 * 1024);

describe('MauiV12FullImagePanelComponent', () => {
  let component: MauiV12FullImagePanelComponent;
  let fixture: ComponentFixture<MauiV12FullImagePanelComponent>;
  let mcuService: jasmine.SpyObj<MauiMcuService>;

  function create(): void {
    fixture = TestBed.createComponent(MauiV12FullImagePanelComponent);
    component = fixture.componentInstance;
    fixture.detectChanges();
  }

  function progress(
    fields: Partial<MauiMcuFullImageProgress>,
  ): MauiMcuFullImageProgress {
    return fields as MauiMcuFullImageProgress;
  }

  beforeEach(async () => {
    localStorage.removeItem(LAST_UART_PORT_KEY);
    mcuService = jasmine.createSpyObj('MauiMcuService', [
      'listUartBridges',
      'listFirmware',
      'updateMcuFullImage',
      'provisionMcuSerial',
    ]);
    mcuService.listUartBridges.and.returnValue(
      of({
        bridges: [FTDI, CP2102],
        defaultPort: '/dev/ttyUSB0',
        message: '2 USB-UART bridge(s) detected.',
      }) as never,
    );
    mcuService.listFirmware.and.returnValue(
      of({
        files: [
          {
            fileName: 'broken.bin',
            path: '/fw/mcu_full/broken.bin',
            sizeBytes: '10',
            expectedSizeBytes: MIB,
          },
          {
            fileName: 'maui_full_flash_1mb_0.69-5324f43.bin',
            path: '/fw/mcu_full/maui_full_flash_1mb_0.69-5324f43.bin',
            sizeBytes: MIB,
            expectedSizeBytes: MIB,
          },
        ],
        message: '2 firmware file(s) found.',
      }) as never,
    );
    await TestBed.configureTestingModule({
      imports: [MauiV12FullImagePanelComponent, NoopAnimationsModule],
      providers: [
        {provide: MauiMcuService, useValue: mcuService},
        {
          provide: MauiDialogService,
          useValue: jasmine.createSpyObj('MauiDialogService', ['openFailure']),
        },
      ],
    }).compileComponents();
  });

  afterEach(() => {
    localStorage.removeItem(LAST_UART_PORT_KEY);
  });

  it('preselects the server default bridge and the newest valid image', () => {
    create();
    expect(mcuService.listFirmware).toHaveBeenCalledWith('mcu_full');
    expect(component.selectedPort).toBe('/dev/ttyUSB0');
    expect(component.selectedImage).toBe(
      'maui_full_flash_1mb_0.69-5324f43.bin',
    );
    expect(component.verify).toBeFalse();
    expect(component.baud).toBe(115200);
    expect(component.canStart).toBeTrue();
    expect(component.bridgeLabel(FTDI as never)).toBe(
      'usb-FTDI_TTL232R-3V3_FT9XYZ-if00-port0 (/dev/ttyUSB0)',
    );
    expect(component.bridgeLabel(CP2102 as never)).toBe(
      'Silicon Labs CP2102N (/dev/ttyUSB1)',
    );
  });

  it('preselects the stable image when it has the right size', () => {
    const files = [
      {
        fileName: 'maui_full_flash_1mb_0.70-aaaaaaa.bin',
        path: '/fw/mcu_full/new.bin',
        sizeBytes: MIB,
        expectedSizeBytes: MIB,
      },
      {
        fileName: 'maui_full_flash_1mb.bin',
        path: '/fw/mcu_full/maui_full_flash_1mb.bin',
        sizeBytes: MIB,
        expectedSizeBytes: MIB,
        isStable: true,
        version: '0.69-5324f43',
      },
    ];
    mcuService.listFirmware.and.returnValue(
      of({files, message: '2 firmware file(s) found.'}) as never,
    );
    create();
    expect(component.selectedImage).toBe('maui_full_flash_1mb.bin');

    // A stable file with the wrong size is never preselected.
    files[1].sizeBytes = '10';
    component.selectedImage = '';
    component.loadImages();
    expect(component.selectedImage).toBe(
      'maui_full_flash_1mb_0.70-aaaaaaa.bin',
    );
  });

  it('prefers the bridge used last', () => {
    localStorage.setItem(LAST_UART_PORT_KEY, '/dev/ttyUSB1');
    create();
    expect(component.selectedPort).toBe('/dev/ttyUSB1');
  });

  it('ignores a remembered bridge that is gone', () => {
    localStorage.setItem(LAST_UART_PORT_KEY, '/dev/serial/by-id/usb-gone');
    create();
    expect(component.selectedPort).toBe('/dev/ttyUSB0');
  });

  it('does not start without a bridge or with a wrong-size image', () => {
    mcuService.listUartBridges.and.returnValue(
      of({bridges: [], defaultPort: '', message: 'No bridge'}) as never,
    );
    create();
    expect(component.canStart).toBeFalse();
    component.start();
    expect(mcuService.updateMcuFullImage).not.toHaveBeenCalled();
    expect(fixture.nativeElement.textContent).toContain('No bridge');
    component.bridges = [FTDI as never];
    component.selectedPort = '/dev/ttyUSB0';
    component.selectedImage = 'broken.bin';
    expect(component.canStart).toBeFalse();
  });

  it('streams the flash, remembers the bridge and offers the serial card', () => {
    const stream = new Subject<MauiMcuFullImageProgress>();
    mcuService.updateMcuFullImage.and.returnValue(stream as never);
    create();
    const busy: boolean[] = [];
    component.busyChange.subscribe(b => busy.push(b));
    component.verify = true;
    component.start();
    expect(busy).toEqual([true]);
    expect(mcuService.updateMcuFullImage).toHaveBeenCalledWith(
      '/dev/ttyUSB0',
      'maui_full_flash_1mb_0.69-5324f43.bin',
      true,
      115200,
    );
    expect(localStorage.getItem(LAST_UART_PORT_KEY)).toBe(FTDI.byId);
    expect(component.inProgress).toBeTrue();
    expect(component.canStart).toBeFalse();

    stream.next(progress({phase: 'connecting', logLine: 'Chip ID = 08 20 02'}));
    stream.next(
      progress({
        phase: 'programming',
        phasePercent: 40,
        progressPercent: 47,
        statusMessage: 'Programming... 40%',
        elapsedS: 75,
      }),
    );
    fixture.detectChanges();
    expect(component.progress).toBe(47);
    expect(component.phaseLabel).toBe('Programming 40%');
    expect(component.elapsedText).toBe('1:15');
    expect(component.log).toEqual(['Chip ID = 08 20 02']);
    const startButton: HTMLButtonElement =
      fixture.nativeElement.querySelector('.start-button');
    expect(startButton.disabled).toBeTrue();
    expect(fixture.nativeElement.textContent).toContain('Programming 40%');

    stream.next(
      progress({
        isComplete: true,
        success: true,
        phase: 'done',
        progressPercent: 100,
        statusMessage: 'MCU full image flashed. Provision the serial again.',
        mcuVersion: '0.69-5324f43',
      }),
    );
    stream.complete();
    fixture.detectChanges();
    expect(component.result).toBe('PASSED');
    expect(component.inProgress).toBeFalse();
    expect(busy).toEqual([true, false]);
    expect(component.progress).toBe(100);
    expect(component.showSerialCard).toBeTrue();
    expect(
      fixture.nativeElement.querySelector('app-maui-serial-provision-card'),
    ).toBeTruthy();
    // Numbered like the serial card of the v1.2 functional suite.
    expect(fixture.nativeElement.textContent).toContain(
      '1. Serial Number Provisioning (after the full flash)',
    );
  });

  it('reports a failed flash without the serial card', () => {
    mcuService.updateMcuFullImage.and.returnValue(
      of(
        progress({phase: 'erasing', phasePercent: 100, progressPercent: 10}),
        progress({
          isComplete: true,
          success: false,
          phase: 'failed',
          progressPercent: 10,
          errorMessage: 'No answer from the MCU within 20 s.',
        }),
      ) as never,
    );
    create();
    component.start();
    fixture.detectChanges();
    expect(component.result).toBe('FAILED');
    expect(component.status).toBe('No answer from the MCU within 20 s.');
    expect(component.showSerialCard).toBeFalse();
    expect(
      fixture.nativeElement.querySelector('app-maui-serial-provision-card'),
    ).toBeNull();
  });

  it('reports gRPC errors', () => {
    mcuService.updateMcuFullImage.and.returnValue(
      throwError(() => new Error('unavailable')),
    );
    create();
    component.start();
    expect(component.result).toBe('FAILED');
    expect(component.inProgress).toBeFalse();
    expect(component.status).toContain('unavailable');
  });

  it('does not start while the page locks it', () => {
    create();
    component.locked = true;
    fixture.detectChanges();
    expect(component.canStart).toBeFalse();
    component.start();
    expect(mcuService.updateMcuFullImage).not.toHaveBeenCalled();
    expect(fixture.nativeElement.textContent).toContain(
      'Wait until the other MCU programming step has finished.',
    );
  });

  it('cancels a running flash when the page is left', () => {
    const stream = new Subject<MauiMcuFullImageProgress>();
    mcuService.updateMcuFullImage.and.returnValue(stream as never);
    create();
    component.start();
    expect(stream.observed).toBeTrue();
    fixture.destroy();
    expect(stream.observed).toBeFalse();
  });
});
