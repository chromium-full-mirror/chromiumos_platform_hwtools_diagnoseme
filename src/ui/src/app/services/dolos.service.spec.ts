// Copyright 2025 The ChromiumOS Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

import { TestBed } from '@angular/core/testing';

import { DolosService } from './dolos.service';

describe('DolosService', () => {
  let service: DolosService;

  beforeEach(() => {
    TestBed.configureTestingModule({});
    service = TestBed.inject(DolosService);
  });

  it('should be created', () => {
    expect(service).toBeTruthy();
  });
});
