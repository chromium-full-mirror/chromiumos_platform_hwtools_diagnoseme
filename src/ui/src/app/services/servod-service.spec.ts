// Copyright 2025 The ChromiumOS Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

import {TestBed} from '@angular/core/testing';
import {GRPC_CLIENT_FACTORY, GrpcHandler} from '@ngx-grpc/core';

import {ServodService} from './servod-service';
import {ServodRpcServiceClient} from '../../proto/diagnoseme-servod.pbsc';

describe('ServodService', () => {
  let service: ServodService;
  let mockClientFactory: jasmine.SpyObj<any>;
  let mockServodClient: jasmine.SpyObj<ServodRpcServiceClient>;

  beforeEach(() => {
    mockServodClient = jasmine.createSpyObj('ServodRpcServiceClient', [
      'startServod',
    ]);
    mockClientFactory = jasmine.createSpyObj('GrpcClientFactory', [
      'createClient',
    ]);
    mockClientFactory.createClient.and.returnValue(mockServodClient);

    TestBed.configureTestingModule({
      providers: [
        ServodService,
        {provide: GRPC_CLIENT_FACTORY, useValue: mockClientFactory},
        {
          provide: GrpcHandler,
          useValue: jasmine.createSpyObj('GrpcHandler', ['handle']),
        },
      ],
    });
    service = TestBed.inject(ServodService);
  });

  it('should be created', () => {
    expect(service).toBeTruthy();
  });
});
