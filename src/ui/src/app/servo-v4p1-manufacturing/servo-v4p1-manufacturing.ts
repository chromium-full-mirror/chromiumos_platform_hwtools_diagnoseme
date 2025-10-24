// Copyright 2025 The ChromiumOS Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

import { Component } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { MatToolbarModule } from '@angular/material/toolbar';
import { DiagnosemeReworkCard } from '../diagnoseme-rework-card/diagnoseme-rework-card';
import { DiagnosemeReworkCardColumn } from '../diagnoseme-rework-card-column/diagnoseme-rework-card-column';
import { HelpDialogButton } from '../help-dialog-button/help-dialog-button';

@Component({
  selector: 'app-servo-v4p1-manufacturing',
  standalone: true,
  templateUrl: './servo-v4p1-manufacturing.html',
  styleUrl: './servo-v4p1-manufacturing.scss',
  imports: [
    DiagnosemeReworkCard,
    DiagnosemeReworkCardColumn,
    FormsModule,
    MatButtonModule,
    MatFormFieldModule,
    MatInputModule,
    HelpDialogButton,
    MatToolbarModule
  ]
})
export class ServoV4p1ManufacturingComponent {
  servoSerialNumber: string = '';
  servoMacAddress: string = '';
}
