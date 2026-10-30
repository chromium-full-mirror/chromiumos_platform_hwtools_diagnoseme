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
import {By} from '@angular/platform-browser';
import {NoopAnimationsModule} from '@angular/platform-browser/animations';
import {provideRouter} from '@angular/router';
import {of} from 'rxjs';
import {MauiStep2FunctionalComponent} from './maui-step2-functional';
import {MauiStation2Service} from '../services/maui-station2.service';
import {MauiDeviceMgmtService} from '../services/maui-device-mgmt.service';
import {MauiDialogService} from '../maui-common/maui-dialog.service';
import {MauiFunctionalSuiteComponent} from '../maui-common/maui-functional-suite/maui-functional-suite';
import {MauiReadSerialResponse} from '../../proto/maui-manufacturing.pb';

describe('MauiStep2FunctionalComponent', () => {
  let fixture: ComponentFixture<MauiStep2FunctionalComponent>;
  let deviceMgmt: jasmine.SpyObj<MauiDeviceMgmtService>;

  beforeEach(async () => {
    deviceMgmt = jasmine.createSpyObj<MauiDeviceMgmtService>(
      'MauiDeviceMgmtService',
      ['readConnectedDeviceSerial'],
    );
    deviceMgmt.readConnectedDeviceSerial.and.returnValue(
      of(new MauiReadSerialResponse({isConnected: false})),
    );

    await TestBed.configureTestingModule({
      imports: [MauiStep2FunctionalComponent, NoopAnimationsModule],
      providers: [
        provideRouter([]),
        {
          provide: MauiStation2Service,
          useValue: jasmine.createSpyObj<MauiStation2Service>(
            'MauiStation2Service',
            ['runHostOnlyTests'],
          ),
        },
        {provide: MauiDeviceMgmtService, useValue: deviceMgmt},
        {
          provide: MauiDialogService,
          useValue: jasmine.createSpyObj<MauiDialogService>(
            'MauiDialogService',
            ['openFailure'],
          ),
        },
      ],
    }).compileComponents();
  });

  it('hosts the functional suite in smt mode', fakeAsync(() => {
    fixture = TestBed.createComponent(MauiStep2FunctionalComponent);
    fixture.detectChanges();
    tick();

    const suite = fixture.debugElement.query(
      By.directive(MauiFunctionalSuiteComponent),
    ).componentInstance as MauiFunctionalSuiteComponent;
    expect(suite.mode).toBe('smt');
    expect(suite.isFatpMode).toBe(false);
    expect(deviceMgmt.readConnectedDeviceSerial).toHaveBeenCalled();
    flush();
    discardPeriodicTasks();
  }));
});
