// Copyright 2025 The ChromiumOS Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

import {ComponentFixture, TestBed} from '@angular/core/testing';

import {HelpDialogButton} from './help-dialog-button';

describe('HelpDialogButton', () => {
  let component: HelpDialogButton;
  let fixture: ComponentFixture<HelpDialogButton>;

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [HelpDialogButton],
    }).compileComponents();

    fixture = TestBed.createComponent(HelpDialogButton);
    component = fixture.componentInstance;
    fixture.detectChanges();
  });

  it('should create', () => {
    expect(component).toBeTruthy();
  });
});
