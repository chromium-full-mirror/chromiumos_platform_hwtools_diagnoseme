// Copyright 2025 The ChromiumOS Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

import {Component, Inject, Input} from '@angular/core';
import {MatButtonModule} from '@angular/material/button';
import {
  MAT_DIALOG_DATA,
  MatDialog,
  MatDialogActions,
  MatDialogContent,
  MatDialogRef,
} from '@angular/material/dialog';
import {MatIconModule} from '@angular/material/icon';

@Component({
  selector: 'help-dialog-button',
  standalone: true,
  templateUrl: './help-dialog-button.html',
  styleUrl: './help-dialog-button.scss',
  imports: [MatIconModule, MatButtonModule],
})
export class HelpDialogButton {
  @Input() image = '';
  constructor(public dialog: MatDialog) {}
  openPicDialog() {
    console.log(this.image);
    this.dialog.open(DialogOverviewExampleDialog, {data: this.image});
  }
}

@Component({
  selector: 'app-help-dialog',
  template: `
    <mat-dialog-content class="image-dialog-content">
      <img
        mat-card-image
        class="card-image"
        src="../assets/img/{{ image }}"
        alt="Photo failed to load."
      />
    </mat-dialog-content>
    <mat-dialog-actions class="image-dialog-content">
      <button mat-raised-button color="primary" (click)="onClose()">
        Close
      </button>
    </mat-dialog-actions>
  `,
  standalone: true,
  imports: [MatDialogContent, MatDialogActions, MatButtonModule],
})
export class DialogOverviewExampleDialog {
  constructor(
    public dialogRef: MatDialogRef<DialogOverviewExampleDialog>,
    @Inject(MAT_DIALOG_DATA) public image: string,
  ) {}

  onClose(): void {
    this.dialogRef.close();
  }
}
