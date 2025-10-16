// Copyright 2025 The ChromiumOS Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

import { Component } from '@angular/core';
import { MatMenuModule} from '@angular/material/menu';
import { MatToolbarModule } from '@angular/material/toolbar';
import { MatIconModule } from '@angular/material/icon';
import { MatCardModule } from '@angular/material/card';
import { DolosProvisionComponent } from './dolos-provision/dolos-provision.component';

@Component({
  selector: 'app-root',
  templateUrl: './app.component.html',
  styleUrl: './app.component.scss',
  imports: [
      MatMenuModule,
      MatToolbarModule,
      MatIconModule,
      MatCardModule,
      DolosProvisionComponent
  ]
})
export class AppComponent {
  title = 'HW Tools Diagnose Me&#8482';
  constructor(
  ) {}


}
