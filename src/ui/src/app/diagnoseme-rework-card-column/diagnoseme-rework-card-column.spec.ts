// Copyright 2025 The ChromiumOS Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

import {ComponentFixture, TestBed} from '@angular/core/testing';

import {DiagnosemeReworkCardColumn} from './diagnoseme-rework-card-column';

describe('DiagnosemeReworkCardColumn', () => {
  let component: DiagnosemeReworkCardColumn;
  let fixture: ComponentFixture<DiagnosemeReworkCardColumn>;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [DiagnosemeReworkCardColumn],
    }).compileComponents();

    fixture = TestBed.createComponent(DiagnosemeReworkCardColumn);
    component = fixture.componentInstance;
    fixture.detectChanges();
  });

  it('should create', () => {
    expect(component).toBeTruthy();
  });
});
