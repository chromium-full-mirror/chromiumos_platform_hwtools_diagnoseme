// Copyright 2026 The ChromiumOS Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

import {Injectable, inject} from '@angular/core';
import {MatDialog} from '@angular/material/dialog';
import {
  MauiFailureDialogComponent,
  MauiFailureDialogResult,
  formatHumanReadableError,
} from '../maui-failure-dialog/maui-failure-dialog';

@Injectable({
  providedIn: 'root',
})
export class MauiDialogService {
  private readonly dialog = inject(MatDialog);

  openFailure(
    title: string,
    error: unknown,
    onRetry?: () => void,
    onBypass?: () => void,
  ): void {
    const dialogRef = this.dialog.open(MauiFailureDialogComponent, {
      data: {
        title,
        errorMessage:
          typeof error === 'string' ? error : formatHumanReadableError(error),
        details: error,
        allowBypass: Boolean(onBypass),
      },
    });

    dialogRef.afterClosed().subscribe((result: MauiFailureDialogResult) => {
      if (result === 'retry' && onRetry) {
        onRetry();
      } else if (result === 'bypass' && onBypass) {
        onBypass();
      }
    });
  }
}
