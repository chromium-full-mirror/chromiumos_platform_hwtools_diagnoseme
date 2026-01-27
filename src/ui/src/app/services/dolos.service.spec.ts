// Copyright 2025 The ChromiumOS Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

import {TestBed} from '@angular/core/testing';
import {GRPC_CLIENT_FACTORY, GrpcHandler} from '@ngx-grpc/core';

import {DolosService} from './dolos.service';
import {DolosRpcServiceClient} from '../../proto/diagnoseme-dolos.pbsc';

describe('DolosService', () => {
  let service: DolosService;
  let mockClientFactory: jasmine.SpyObj<any>;
  let mockDolosClient: jasmine.SpyObj<DolosRpcServiceClient>;

  beforeEach(() => {
    mockDolosClient = jasmine.createSpyObj('DolosRpcServiceClient', [
      'getFirmwareVersions',
    ]);
    mockClientFactory = jasmine.createSpyObj('GrpcClientFactory', [
      'createClient',
    ]);
    mockClientFactory.createClient.and.returnValue(mockDolosClient);

    TestBed.configureTestingModule({
      providers: [
        DolosService,
        {provide: GRPC_CLIENT_FACTORY, useValue: mockClientFactory},
        {
          provide: GrpcHandler,
          useValue: jasmine.createSpyObj('GrpcHandler', ['handle']),
        },
      ],
    });
    service = TestBed.inject(DolosService);
  });

  it('should be created', () => {
    expect(service).toBeTruthy();
  });
});
