// Copyright 2025 The ChromiumOS Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

import {Component, Input} from '@angular/core';

@Component({
  selector: 'diagnoseme-rework-card-column',
  standalone: true,
  templateUrl: './diagnoseme-rework-card-column.html',
  styleUrl: './diagnoseme-rework-card-column.scss',
})
export class DiagnosemeReworkCardColumn {
  @Input() column = '';
}
