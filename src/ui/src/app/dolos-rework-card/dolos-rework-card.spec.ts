// Copyright 2025 The ChromiumOS Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

import { ComponentFixture, TestBed } from '@angular/core/testing';

import { DolosReworkCard } from './dolos-rework-card';

describe('DolosReworkCard', () => {
  let component: DolosReworkCard;
  let fixture: ComponentFixture<DolosReworkCard>;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      declarations: [DolosReworkCard]
    })
    .compileComponents();

    fixture = TestBed.createComponent(DolosReworkCard);
    component = fixture.componentInstance;
    fixture.detectChanges();
  });

  it('should create', () => {
    expect(component).toBeTruthy();
  });
});
