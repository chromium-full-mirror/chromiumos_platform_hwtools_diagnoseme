// Copyright 2025 The ChromiumOS Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

import { Component } from '@angular/core';

import { DolosService } from './services/dolos.service';

@Component({
  selector: 'app-root',
  templateUrl: './app.component.html',
  styleUrl: './app.component.scss'
})
export class AppComponent {
  title = 'HW Tools Diagnose Me&#8482';
  constructor(
    private dolosService: DolosService,
  ) {}


}
