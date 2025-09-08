// Copyright 2025 The ChromiumOS Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

import { ComponentFixture, TestBed } from '@angular/core/testing';

import { DolosReworkCardColumn } from './dolos-rework-card-column';

describe('DolosReworkCardColumn', () => {
  let component: DolosReworkCardColumn;
  let fixture: ComponentFixture<DolosReworkCardColumn>;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      declarations: [DolosReworkCardColumn]
    })
    .compileComponents();

    fixture = TestBed.createComponent(DolosReworkCardColumn);
    component = fixture.componentInstance;
    fixture.detectChanges();
  });

  it('should create', () => {
    expect(component).toBeTruthy();
  });
});
