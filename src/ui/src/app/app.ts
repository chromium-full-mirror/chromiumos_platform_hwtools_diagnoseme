// Copyright 2025 The ChromiumOS Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

import { Component } from '@angular/core';
import { MatMenuModule} from '@angular/material/menu';
import { MatToolbarModule } from '@angular/material/toolbar';
import { MatIconModule } from '@angular/material/icon';
import { MatCardModule } from '@angular/material/card';
import { RouterModule } from '@angular/router';

@Component({
  selector: 'app-root',
  templateUrl: './app.html',
  styleUrl: './app.scss',
  imports: [
      MatMenuModule,
      MatToolbarModule,
      MatIconModule,
      MatCardModule,
      RouterModule,
  ]
})
export class AppComponent {
  title = 'HW Tools Diagnose Me&#8482';
  constructor(
  ) {}


}
