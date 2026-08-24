// Copyright 2026 The ChromiumOS Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

import {ComponentFixture, TestBed} from '@angular/core/testing';
import {MauiInitialFlashingComponent} from './maui-initial-flashing';
import {MauiStation1Service} from '../services/maui-station1.service';
import {MauiDeviceMgmtService} from '../services/maui-device-mgmt.service';
import {of} from 'rxjs';
import {MatDialogModule} from '@angular/material/dialog';
import {NoopAnimationsModule} from '@angular/platform-browser/animations';
import {provideRouter} from '@angular/router';

describe('MauiInitialFlashingComponent', () => {
  let component: MauiInitialFlashingComponent;
  let fixture: ComponentFixture<MauiInitialFlashingComponent>;
  let mockMauiService: jasmine.SpyObj<MauiStation1Service>;
  let mockDeviceMgmtService: jasmine.SpyObj<MauiDeviceMgmtService>;

  beforeEach(async () => {
    mockMauiService = jasmine.createSpyObj('MauiStation1Service', [
      'streamBslAutoDiscovery',
      'autoProgramMcuBsl',
      'discoverTopologyAndSerial',
      'programFtdiEeprom',
    ]);

    mockDeviceMgmtService = jasmine.createSpyObj('MauiDeviceMgmtService', [
      'saveCardLog',
      'readConnectedDeviceSerial',
    ]);
    mockDeviceMgmtService.saveCardLog.and.returnValue(
      of({success: true, savedFilePath: '', message: 'Saved'} as any),
    );

    mockMauiService.streamBslAutoDiscovery.and.returnValue(
      of({
        bslDetected: false,
        devicePort: '',
        timeRemainingSec: 10,
        message: 'Waiting...',
      } as any),
    );

    await TestBed.configureTestingModule({
      imports: [
        MauiInitialFlashingComponent,
        MatDialogModule,
        NoopAnimationsModule,
      ],
      providers: [
        provideRouter([]),
        {provide: MauiStation1Service, useValue: mockMauiService},
        {provide: MauiDeviceMgmtService, useValue: mockDeviceMgmtService},
      ],
    }).compileComponents();

    fixture = TestBed.createComponent(MauiInitialFlashingComponent);
    component = fixture.componentInstance;
    fixture.detectChanges();
  });

  it('should create component', () => {
    expect(component).toBeTruthy();
  });
});
