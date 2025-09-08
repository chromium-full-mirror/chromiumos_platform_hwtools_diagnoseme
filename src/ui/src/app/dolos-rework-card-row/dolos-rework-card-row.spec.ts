// Copyright 2025 The ChromiumOS Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

import { ComponentFixture, TestBed } from '@angular/core/testing';

import { DolosReworkCardRow } from './dolos-rework-card-row';

describe('DolosReworkCardRow', () => {
  let component: DolosReworkCardRow;
  let fixture: ComponentFixture<DolosReworkCardRow>;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      declarations: [DolosReworkCardRow]
    })
    .compileComponents();

    fixture = TestBed.createComponent(DolosReworkCardRow);
    component = fixture.componentInstance;
    fixture.detectChanges();
  });

  it('should create', () => {
    expect(component).toBeTruthy();
  });
});
