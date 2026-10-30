// Copyright 2026 The ChromiumOS Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

import {Component, Inject} from '@angular/core';
import {CommonModule} from '@angular/common';
import {MatButtonModule} from '@angular/material/button';
import {MatIconModule} from '@angular/material/icon';
import {
  MAT_DIALOG_DATA,
  MatDialogModule,
  MatDialogRef,
} from '@angular/material/dialog';

export interface MauiFailureDialogData {
  title: string;
  errorMessage?: string;
  details?: unknown;
  allowBypass?: boolean;
}

export type MauiFailureDialogResult = 'retry' | 'bypass' | 'close';

/** Turns an error (string, Error, gRPC status, nested error) into text. */
export function formatHumanReadableError(err: unknown): string {
  if (!err) {
    return 'An unknown error occurred.';
  }
  if (typeof err === 'string') {
    return err;
  }
  if (err instanceof Error) {
    return err.message;
  }
  if (typeof err === 'object') {
    const e = err as {
      message?: unknown;
      statusMessage?: unknown;
      statusCode?: unknown;
      error?: unknown;
    };
    if (e.message && typeof e.message === 'string') {
      return e.message;
    }
    if (e.statusMessage && typeof e.statusMessage === 'string') {
      return `gRPC Status (${e.statusCode || 'Error'}): ${e.statusMessage}`;
    }
    if (e.error) {
      return formatHumanReadableError(e.error);
    }
  }
  try {
    const jsonStr = JSON.stringify(err, null, 2);
    return jsonStr === '{}' ? String(err) : jsonStr;
  } catch {
    return String(err);
  }
}

@Component({
  selector: 'app-maui-failure-dialog',
  standalone: true,
  templateUrl: './maui-failure-dialog.html',
  styleUrl: './maui-failure-dialog.scss',
  imports: [CommonModule, MatButtonModule, MatIconModule, MatDialogModule],
})
export class MauiFailureDialogComponent {
  title: string;
  formattedError: string;
  allowBypass: boolean;

  constructor(
    public dialogRef: MatDialogRef<
      MauiFailureDialogComponent,
      MauiFailureDialogResult
    >,
    @Inject(MAT_DIALOG_DATA) public data: MauiFailureDialogData,
  ) {
    this.title = data.title || 'Step Verification Issue';
    this.allowBypass = data.allowBypass !== false;
    const msg = data.errorMessage || '';
    const det = data.details ? formatHumanReadableError(data.details) : '';
    if (msg && det && msg !== det) {
      this.formattedError = `${msg}\n\nDetails:\n${det}`;
    } else {
      this.formattedError = msg || det || 'Test condition did not pass.';
    }
  }

  onRetry(): void {
    this.dialogRef.close('retry');
  }

  onBypass(): void {
    this.dialogRef.close('bypass');
  }

  onClose(): void {
    this.dialogRef.close('close');
  }
}
