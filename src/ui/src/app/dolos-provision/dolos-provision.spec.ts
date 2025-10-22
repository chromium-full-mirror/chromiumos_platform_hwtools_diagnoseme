// Copyright 2025 The ChromiumOS Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

import { ComponentFixture, TestBed } from '@angular/core/testing';

import { DolosProvisionComponent } from './dolos-provision';

describe('DolosProvisionComponent', () => {
  let component: DolosProvisionComponent;
  let fixture: ComponentFixture<DolosProvisionComponent>;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      declarations: [DolosProvisionComponent]
    })
    .compileComponents();

    fixture = TestBed.createComponent(DolosProvisionComponent);
    component = fixture.componentInstance;
    fixture.detectChanges();
  });

  it('should create', () => {
    expect(component).toBeTruthy();
  });
});
