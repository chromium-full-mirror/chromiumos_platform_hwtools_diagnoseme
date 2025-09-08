// Copyright 2025 The ChromiumOS Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

import { CommonModule } from '@angular/common';
import { Component, Input } from '@angular/core';
import { MatCardModule } from '@angular/material/card';
import { MatProgressBarModule } from '@angular/material/progress-bar';


@Component({
  selector: 'app-dolos-rework-card',
  standalone: true,
  templateUrl: './dolos-rework-card.html',
  styleUrl: './dolos-rework-card.scss',
  imports: [
    MatCardModule,
    MatProgressBarModule,
    CommonModule
  ]
})
export class DolosReworkCard {
  @Input() title: string = "Placeholder for Title";
  @Input() progress: boolean = false;
}
