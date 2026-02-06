// Copyright 2025 The ChromiumOS Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

import {CommonModule} from '@angular/common';
import {Component, Input} from '@angular/core';
import {MatCardModule} from '@angular/material/card';
import {MatProgressBarModule} from '@angular/material/progress-bar';

@Component({
  selector: 'diagnoseme-rework-card',
  standalone: true,
  templateUrl: './diagnoseme-rework-card.html',
  styleUrl: './diagnoseme-rework-card.scss',
  imports: [MatCardModule, MatProgressBarModule, CommonModule],
})
export class DiagnosemeReworkCard {
  @Input() title = 'Placeholder for Title';
  @Input() progress = false;
  @Input() disabled = false;
}
