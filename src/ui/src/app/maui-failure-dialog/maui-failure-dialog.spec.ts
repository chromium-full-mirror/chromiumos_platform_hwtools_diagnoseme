// Copyright 2026 The ChromiumOS Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

import {ComponentFixture, TestBed} from '@angular/core/testing';
import {MAT_DIALOG_DATA, MatDialogRef} from '@angular/material/dialog';
import {MauiFailureDialogComponent} from './maui-failure-dialog';

describe('MauiFailureDialogComponent', () => {
  let component: MauiFailureDialogComponent;
  let fixture: ComponentFixture<MauiFailureDialogComponent>;

  const mockDialogRef = {
    close: jasmine.createSpy('close'),
  };

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [MauiFailureDialogComponent],
      providers: [
        {provide: MatDialogRef, useValue: mockDialogRef},
        {
          provide: MAT_DIALOG_DATA,
          useValue: {
            title: 'Test Failure',
            errorMessage: 'Something went wrong',
            allowBypass: true,
          },
        },
      ],
    }).compileComponents();

    fixture = TestBed.createComponent(MauiFailureDialogComponent);
    component = fixture.componentInstance;
    fixture.detectChanges();
  });

  it('should create', () => {
    expect(component).toBeTruthy();
  });
});
