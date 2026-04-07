// Copyright 2026 The ChromiumOS Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

import {ComponentFixture, TestBed} from '@angular/core/testing';
import {ServoMicroManufacturingComponent} from './servo-micro-manufacturing';
import {ServoMicroManufacturingService} from '../services/servo-micro-manufacturing.service';
import {of} from 'rxjs';

describe('ServoMicroManufacturingComponent', () => {
  let component: ServoMicroManufacturingComponent;
  let fixture: ComponentFixture<ServoMicroManufacturingComponent>;
  let mockService: jasmine.SpyObj<ServoMicroManufacturingService>;

  beforeEach(async () => {
    mockService = jasmine.createSpyObj('ServoMicroManufacturingService', [
      'validateServoSerial',
      'programMcu',
      'programSerial',
      'runTests',
      'submitProvisioningResults',
    ]);
    mockService.validateServoSerial.and.returnValue(
      of({isValid: true, error: ''} as any),
    );

    await TestBed.configureTestingModule({
      imports: [ServoMicroManufacturingComponent],
      providers: [
        {provide: ServoMicroManufacturingService, useValue: mockService},
      ],
    }).compileComponents();

    fixture = TestBed.createComponent(ServoMicroManufacturingComponent);
    component = fixture.componentInstance;
    fixture.detectChanges();
  });

  it('should create', () => {
    expect(component).toBeTruthy();
  });
});
