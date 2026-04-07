// Copyright 2026 The ChromiumOS Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

import {ComponentFixture, TestBed} from '@angular/core/testing';
import {of} from 'rxjs';

import {ServoV4p1ManufacturingComponent} from './servo-v4p1-manufacturing';
import {ServoManufacturingService} from '../services/servo-manufacturing.service';
import {NoopAnimationsModule} from '@angular/platform-browser/animations';
import {ValidationResponse} from '../../proto/servo-manufacturing.pb';

describe('ServoV4p1ManufacturingComponent', () => {
  let component: ServoV4p1ManufacturingComponent;
  let fixture: ComponentFixture<ServoV4p1ManufacturingComponent>;
  let mockServoManufacturingService: jasmine.SpyObj<ServoManufacturingService>;

  beforeEach(async () => {
    mockServoManufacturingService = jasmine.createSpyObj(
      'ServoManufacturingService',
      ['validateServoSerial', 'validateServoMacAddress'],
    );
    const mockValidationResponse = new ValidationResponse();
    mockValidationResponse.isValid = true;
    mockServoManufacturingService.validateServoSerial.and.returnValue(
      of(mockValidationResponse),
    );
    mockServoManufacturingService.validateServoMacAddress.and.returnValue(
      of(mockValidationResponse),
    );

    await TestBed.configureTestingModule({
      imports: [ServoV4p1ManufacturingComponent, NoopAnimationsModule],
      providers: [
        {
          provide: ServoManufacturingService,
          useValue: mockServoManufacturingService,
        },
      ],
    }).compileComponents();

    fixture = TestBed.createComponent(ServoV4p1ManufacturingComponent);
    component = fixture.componentInstance;
    fixture.detectChanges();
  });

  it('should create', () => {
    expect(component).toBeTruthy();
  });

  it('should focus the serial number input on init', () => {
    const inputElement = fixture.nativeElement.querySelector(
      'input[placeholder="SERVOV4P1_x_xxxxxxxxxx"]',
    );
    expect(document.activeElement).toBe(inputElement);
  });
});
