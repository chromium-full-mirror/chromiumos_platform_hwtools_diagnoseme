// Copyright 2025 The ChromiumOS Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

import { ComponentFixture, TestBed } from '@angular/core/testing';

import { ServoV4p1ManufacturingComponent } from './servo-v4p1-manufacturing';

describe('ServoV4p1ManufacturingComponent', () => {
  let component: ServoV4p1ManufacturingComponent;
  let fixture: ComponentFixture<ServoV4p1ManufacturingComponent>;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      declarations: [ServoV4p1ManufacturingComponent]
    })
    .compileComponents();

    fixture = TestBed.createComponent(ServoV4p1ManufacturingComponent);
    component = fixture.componentInstance;
    fixture.detectChanges();
  });

  it('should create', () => {
    expect(component).toBeTruthy();
  });
});
