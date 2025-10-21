// Copyright 2025 The ChromiumOS Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

import { ComponentFixture, TestBed } from '@angular/core/testing';

import { ServoManufacturingComponent } from './servo-manufacturing.component';

describe('ServoManufacturingComponent', () => {
  let component: ServoManufacturingComponent;
  let fixture: ComponentFixture<ServoManufacturingComponent>;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      declarations: [ServoManufacturingComponent]
    })
    .compileComponents();

    fixture = TestBed.createComponent(ServoManufacturingComponent);
    component = fixture.componentInstance;
    fixture.detectChanges();
  });

  it('should create', () => {
    expect(component).toBeTruthy();
  });
});
