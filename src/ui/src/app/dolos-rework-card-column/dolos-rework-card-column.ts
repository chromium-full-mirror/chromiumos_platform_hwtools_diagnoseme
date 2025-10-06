// Copyright 2025 The ChromiumOS Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

import { Component, Input } from '@angular/core';

@Component({
  selector: 'dolos-rework-card-column',
  standalone: true,
  templateUrl: './dolos-rework-card-column.html',
  styleUrl: './dolos-rework-card-column.scss'
})
export class DolosReworkCardColumn {
  @Input() column: string = "";
}
