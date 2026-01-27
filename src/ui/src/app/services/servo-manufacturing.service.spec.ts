// Copyright 2026 The ChromiumOS Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

import {TestBed} from '@angular/core/testing';
import {GrpcHandler} from '@ngx-grpc/core';
import {ServoV41ManufacturingClient} from '../../proto/servo-manufacturing.pbsc';
import {
  ValidationResponse,
  ValidateServoSerialRequest,
} from '../../proto/servo-manufacturing.pb';
import {ServoManufacturingService} from './servo-manufacturing.service';
import {of} from 'rxjs';

describe('ServoManufacturingService', () => {
  let service: ServoManufacturingService;
  let mockClient: jasmine.SpyObj<ServoV41ManufacturingClient>;

  beforeEach(() => {
    mockClient = jasmine.createSpyObj('ServoV41ManufacturingClient', [
      'validateServoSerial',
      'getStatus',
      'programGenesysHub',
      'programMcu',
      'programEthernet',
      'runTests',
      'validateServoMacAddress',
    ]);

    TestBed.configureTestingModule({
      providers: [
        ServoManufacturingService,
        {provide: ServoV41ManufacturingClient, useValue: mockClient},
        {
          provide: GrpcHandler,
          useValue: jasmine.createSpyObj('GrpcHandler', ['handle']),
        },
      ],
    });
    service = TestBed.inject(ServoManufacturingService);
  });

  it('should be created', () => {
    expect(service).toBeTruthy();
  });

  describe('validateServoSerial', () => {
    it('should call client.validateServoSerial with correct request', () => {
      const serialNumber = 'SERVOV4P1-C-2210050007';
      const expectedRequest = new ValidateServoSerialRequest();
      expectedRequest.serialNumber = serialNumber;
      const expectedResponse = new ValidationResponse();
      expectedResponse.isValid = true;

      mockClient.validateServoSerial.and.returnValue(of(expectedResponse));

      service.validateServoSerial(serialNumber).subscribe(response => {
        expect(response).toEqual(expectedResponse);
      });

      expect(mockClient.validateServoSerial).toHaveBeenCalledWith(
        expectedRequest,
      );
    });
  });
});
