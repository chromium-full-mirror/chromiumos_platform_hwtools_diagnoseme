// Copyright 2025 The ChromiumOS Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

import {ComponentFixture, TestBed} from '@angular/core/testing';

import {DiagnosemeReworkCard} from './diagnoseme-rework-card';

describe('DiagnosemeReworkCard', () => {
  let component: DiagnosemeReworkCard;
  let fixture: ComponentFixture<DiagnosemeReworkCard>;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [DiagnosemeReworkCard],
    }).compileComponents();

    fixture = TestBed.createComponent(DiagnosemeReworkCard);
    component = fixture.componentInstance;
    fixture.detectChanges();
  });

  it('should create', () => {
    expect(component).toBeTruthy();
  });
});
