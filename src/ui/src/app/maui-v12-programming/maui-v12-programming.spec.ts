// Copyright 2026 The ChromiumOS Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

import {ComponentFixture, TestBed} from '@angular/core/testing';
import {NoopAnimationsModule} from '@angular/platform-browser/animations';
import {of} from 'rxjs';
import {MauiV12ProgrammingComponent} from './maui-v12-programming';
import {MauiMcuService} from '../services/maui-mcu.service';
import {MauiPdcService} from '../services/maui-pdc.service';

describe('MauiV12ProgrammingComponent', () => {
  let component: MauiV12ProgrammingComponent;
  let fixture: ComponentFixture<MauiV12ProgrammingComponent>;
  let mcuService: jasmine.SpyObj<MauiMcuService>;

  beforeEach(async () => {
    mcuService = jasmine.createSpyObj('MauiMcuService', [
      'getUnitStatus',
      'listFirmware',
      'listUartBridges',
      'updateMcuApp',
      'listH2hFirmware',
    ]);
    mcuService.listH2hFirmware.and.returnValue(
      of({
        directory: '/home/diagnoseme/diagnoseme_h2h_firmware',
        directoryExists: true,
        files: [],
        message: 'No H2H image (*.bin)',
      }) as never,
    );
    mcuService.listUartBridges.and.returnValue(
      of({bridges: [], defaultPort: '', message: 'none'}) as never,
    );
    mcuService.getUnitStatus.and.returnValue(
      of({
        h2hSerial: 'MAUIV12H26010001',
        mcu: {devicePort: '/dev/ttyACM0', serialNumber: 'MAUIV12H26010001'},
        consoles: [
          {
            devicePort: '/dev/ttyACM0',
            serialNumber: 'MAUIV12H26010001',
            stage: 'app',
          },
        ],
      }) as never,
    );
    mcuService.listFirmware.and.returnValue(
      of({
        files: [{fileName: 'maui_app_signed.bin', path: '/x'}],
        message: '1 file',
      }) as never,
    );
    const pdcService = jasmine.createSpyObj('MauiPdcService', [
      'discoverConnectedPdcDevices',
      'programSinglePdc',
    ]);
    pdcService.discoverConnectedPdcDevices.and.returnValue(
      of({success: true, devices: [], message: 'none'}) as never,
    );
    await TestBed.configureTestingModule({
      imports: [MauiV12ProgrammingComponent, NoopAnimationsModule],
      providers: [
        {provide: MauiMcuService, useValue: mcuService},
        {provide: MauiPdcService, useValue: pdcService},
      ],
    }).compileComponents();

    fixture = TestBed.createComponent(MauiV12ProgrammingComponent);
    component = fixture.componentInstance;
    fixture.detectChanges();
  });

  it('preselects the detected MCU and newest firmware', () => {
    expect(component.selectedPort).toBe('/dev/ttyACM0');
    expect(component.selectedFile).toBe('maui_app_signed.bin');
    expect(mcuService.listFirmware).toHaveBeenCalledWith('mcu_app');
  });

  it('offers only App images and preselects the stable one', () => {
    mcuService.listFirmware.and.returnValue(
      of({
        files: [
          {fileName: 'maui_app_test.bin', path: '/a', sizeBytes: '4096'},
          {
            fileName: 'maui_app_signed.bin',
            path: '/b',
            sizeBytes: '4096',
            isStable: true,
            version: '0.69-5324f43',
          },
          {fileName: 'maui_full_flash_1mb.bin', path: '/c', sizeBytes: '4096'},
          {fileName: 'renamed.bin', path: '/d', sizeBytes: '1048576'},
        ],
        message: '4 files',
      }) as never,
    );
    component.selectedFile = '';
    component.refresh();
    expect(component.firmwareFiles.map(f => f.fileName)).toEqual([
      'maui_app_test.bin',
      'maui_app_signed.bin',
    ]);
    expect(component.selectedFile).toBe('maui_app_signed.bin');
  });

  it('names the two MCU programming options', () => {
    const text = (fixture.nativeElement.textContent as string).replace(
      /\s+/g,
      ' ',
    );
    expect(text).toContain('MCU App update over USB (RW bootloader)');
    expect(text).toContain(
      'Full MCU image (1 MiB) over USB-UART with itecomdbgr',
    );
  });

  it('hosts the full image panel and no H2H placeholder', () => {
    const page: HTMLElement = fixture.nativeElement;
    expect(page.querySelector('app-maui-v12-full-image-panel')).toBeTruthy();
    expect(mcuService.listUartBridges).toHaveBeenCalled();
    expect(mcuService.listFirmware).toHaveBeenCalledWith('mcu_full');
    expect(page.textContent).not.toContain('H2H Bridge Flash');
    expect(page.textContent).not.toContain('Not available yet');
  });

  it('blocks the App update while a full image flash runs', () => {
    component.fullImageBusy = true;
    fixture.detectChanges();
    component.updateMcuApp();
    expect(mcuService.updateMcuApp).not.toHaveBeenCalled();
    expect(fixture.nativeElement.textContent).toContain(
      'A full image flash is running.',
    );
  });

  it('hosts the H2H panel and locks the other steps while it flashes', () => {
    const page: HTMLElement = fixture.nativeElement;
    expect(page.querySelector('app-maui-v12-h2h-flash-panel')).toBeTruthy();
    expect(mcuService.listH2hFirmware).toHaveBeenCalled();
    expect(page.textContent).toContain(
      '/home/diagnoseme/diagnoseme_h2h_firmware',
    );
    mcuService.getUnitStatus.calls.reset();
    component.onH2hBusy(true);
    component.updateMcuApp();
    expect(mcuService.updateMcuApp).not.toHaveBeenCalled();
    expect(page.textContent).toContain('An H2H firmware flash is running.');
    component.onH2hBusy(false);
    // The serial numbers may have changed: the unit status is read again.
    expect(mcuService.getUnitStatus).toHaveBeenCalled();
    expect(page.textContent).not.toContain('An H2H firmware flash is running.');
  });

  it('streams the MCU App update', () => {
    mcuService.updateMcuApp.and.returnValue(
      of(
        {progressPercent: 40, statusMessage: 'Uploading', logLine: 'l1'},
        {
          isComplete: true,
          success: true,
          progressPercent: 100,
          statusMessage: 'MCU App updated (version 0.59.2).',
        },
      ) as never,
    );
    component.updateMcuApp();
    expect(mcuService.updateMcuApp).toHaveBeenCalledWith(
      '/dev/ttyACM0',
      '',
      'maui_app_signed.bin',
    );
    expect(component.appResult).toBe('PASSED');
    expect(component.appLog).toEqual(['l1']);
    expect(component.appUpdateInProgress).toBeFalse();
  });

  it('reports a failed update', () => {
    mcuService.updateMcuApp.and.returnValue(
      of({
        isComplete: true,
        success: false,
        errorMessage: 'maui-flash failed',
      }) as never,
    );
    component.updateMcuApp();
    expect(component.appResult).toBe('FAILED');
    expect(component.appStatus).toBe('maui-flash failed');
  });
});
