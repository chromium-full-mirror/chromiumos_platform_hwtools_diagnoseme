// Copyright 2026 The ChromiumOS Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

import {ComponentFixture, TestBed} from '@angular/core/testing';
import {MauiPdcFlashingComponent} from './maui-pdc-flashing';
import {MauiPdcService} from '../services/maui-pdc.service';
import {MauiDeviceMgmtService} from '../services/maui-device-mgmt.service';
import {of} from 'rxjs';
import {MauiConnectedPdcDevicesResponse} from '../../proto/maui-manufacturing.pb';
import {NoopAnimationsModule} from '@angular/platform-browser/animations';
import {provideRouter} from '@angular/router';

describe('MauiPdcFlashingComponent', () => {
  let component: MauiPdcFlashingComponent;
  let fixture: ComponentFixture<MauiPdcFlashingComponent>;
  let mockPdcService: jasmine.SpyObj<MauiPdcService>;
  let mockDeviceMgmtService: jasmine.SpyObj<MauiDeviceMgmtService>;

  beforeEach(async () => {
    mockPdcService = jasmine.createSpyObj('MauiPdcService', [
      'discoverConnectedPdcDevices',
      'programSinglePdc',
    ]);
    mockDeviceMgmtService = jasmine.createSpyObj('MauiDeviceMgmtService', [
      'saveCardLog',
    ]);
    mockDeviceMgmtService.saveCardLog.and.returnValue(
      of({success: true, savedFilePath: '', message: 'Saved'} as any),
    );

    const emptyResp = new MauiConnectedPdcDevicesResponse();
    emptyResp.success = true;
    emptyResp.devices = [];
    mockPdcService.discoverConnectedPdcDevices.and.returnValue(of(emptyResp));

    await TestBed.configureTestingModule({
      imports: [MauiPdcFlashingComponent, NoopAnimationsModule],
      providers: [
        provideRouter([]),
        {provide: MauiPdcService, useValue: mockPdcService},
        {provide: MauiDeviceMgmtService, useValue: mockDeviceMgmtService},
      ],
    }).compileComponents();

    fixture = TestBed.createComponent(MauiPdcFlashingComponent);
    component = fixture.componentInstance;
    fixture.detectChanges();
  });

  it('should create', () => {
    expect(component).toBeTruthy();
  });

  it('should initialize 4 device slots', () => {
    expect(component.deviceSlots.length).toBe(4);
  });
});
