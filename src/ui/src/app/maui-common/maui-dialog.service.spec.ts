// Copyright 2026 The ChromiumOS Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

import {TestBed} from '@angular/core/testing';
import {MatDialog, MatDialogRef} from '@angular/material/dialog';
import {Subject} from 'rxjs';
import {MauiDialogService} from './maui-dialog.service';
import {
  MauiFailureDialogComponent,
  MauiFailureDialogResult,
} from '../maui-failure-dialog/maui-failure-dialog';

describe('MauiDialogService', () => {
  let service: MauiDialogService;
  let matDialog: jasmine.SpyObj<MatDialog>;
  let closed$: Subject<MauiFailureDialogResult | undefined>;

  beforeEach(() => {
    closed$ = new Subject<MauiFailureDialogResult | undefined>();
    matDialog = jasmine.createSpyObj<MatDialog>('MatDialog', ['open']);
    matDialog.open.and.returnValue({
      afterClosed: () => closed$,
    } as unknown as MatDialogRef<MauiFailureDialogComponent>);
    TestBed.configureTestingModule({
      providers: [{provide: MatDialog, useValue: matDialog}],
    });
    service = TestBed.inject(MauiDialogService);
  });

  it('opens the failure dialog with bypass only when a bypass handler is given', () => {
    service.openFailure('Title', 'msg', () => {});
    expect(matDialog.open).toHaveBeenCalledWith(MauiFailureDialogComponent, {
      data: jasmine.objectContaining({
        title: 'Title',
        errorMessage: 'msg',
        allowBypass: false,
      }),
    });

    service.openFailure(
      'Title',
      new Error('boom'),
      () => {},
      () => {},
    );
    expect(matDialog.open.calls.mostRecent().args[1]?.data).toEqual(
      jasmine.objectContaining({errorMessage: 'boom', allowBypass: true}),
    );
  });

  it('calls the handler matching the dialog result', () => {
    const retry = jasmine.createSpy('retry');
    const bypass = jasmine.createSpy('bypass');
    service.openFailure('Title', 'msg', retry, bypass);

    closed$.next('bypass');
    expect(bypass).toHaveBeenCalledTimes(1);
    expect(retry).not.toHaveBeenCalled();
  });

  it('does nothing when the dialog is dismissed', () => {
    const retry = jasmine.createSpy('retry');
    const bypass = jasmine.createSpy('bypass');
    service.openFailure('Title', 'msg', retry, bypass);

    closed$.next(undefined);
    expect(retry).not.toHaveBeenCalled();
    expect(bypass).not.toHaveBeenCalled();
  });
});
