// Copyright 2026 The ChromiumOS Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

import {ComponentFixture, TestBed} from '@angular/core/testing';
import {MAT_DIALOG_DATA, MatDialogRef} from '@angular/material/dialog';
import {
  MauiFailureDialogComponent,
  MauiFailureDialogData,
  MauiFailureDialogResult,
  formatHumanReadableError,
} from './maui-failure-dialog';

describe('MauiFailureDialogComponent', () => {
  let dialogRef: jasmine.SpyObj<
    MatDialogRef<MauiFailureDialogComponent, MauiFailureDialogResult>
  >;

  function create(
    data: MauiFailureDialogData,
  ): ComponentFixture<MauiFailureDialogComponent> {
    TestBed.overrideProvider(MAT_DIALOG_DATA, {useValue: data});
    const fixture = TestBed.createComponent(MauiFailureDialogComponent);
    fixture.detectChanges();
    return fixture;
  }

  function buttons(fixture: ComponentFixture<MauiFailureDialogComponent>): {
    retry: HTMLButtonElement;
    bypass: HTMLButtonElement | null;
  } {
    const el = fixture.nativeElement as HTMLElement;
    return {
      retry: el.querySelector('.retry-btn') as HTMLButtonElement,
      bypass: el.querySelector<HTMLButtonElement>('.bypass-btn'),
    };
  }

  beforeEach(async () => {
    dialogRef = jasmine.createSpyObj<
      MatDialogRef<MauiFailureDialogComponent, MauiFailureDialogResult>
    >('MatDialogRef', ['close']);
    await TestBed.configureTestingModule({
      imports: [MauiFailureDialogComponent],
      providers: [
        {provide: MatDialogRef, useValue: dialogRef},
        {provide: MAT_DIALOG_DATA, useValue: {}},
      ],
    }).compileComponents();
  });

  it('shows the message and the details', () => {
    const fixture = create({
      title: 'Test Failure',
      errorMessage: 'Something went wrong',
      details: new Error('serial port busy'),
    });
    expect(fixture.componentInstance.title).toBe('Test Failure');
    expect(fixture.componentInstance.formattedError).toBe(
      'Something went wrong\n\nDetails:\nserial port busy',
    );
  });

  it('closes with "retry" and "bypass"', () => {
    const fixture = create({title: 'Fail', allowBypass: true});
    buttons(fixture).retry.click();
    expect(dialogRef.close).toHaveBeenCalledWith('retry');
    buttons(fixture).bypass!.click();
    expect(dialogRef.close).toHaveBeenCalledWith('bypass');
  });

  it('hides the bypass button when bypass is not allowed', () => {
    const fixture = create({title: 'Fail', allowBypass: false});
    expect(buttons(fixture).bypass).toBeNull();
  });
});

describe('formatHumanReadableError', () => {
  it('formats strings, errors and gRPC statuses', () => {
    expect(formatHumanReadableError(undefined)).toBe(
      'An unknown error occurred.',
    );
    expect(formatHumanReadableError('plain')).toBe('plain');
    expect(formatHumanReadableError(new Error('boom'))).toBe('boom');
    expect(
      formatHumanReadableError({statusCode: 14, statusMessage: 'unavailable'}),
    ).toBe('gRPC Status (14): unavailable');
    expect(formatHumanReadableError({error: {message: 'nested'}})).toBe(
      'nested',
    );
  });
});
