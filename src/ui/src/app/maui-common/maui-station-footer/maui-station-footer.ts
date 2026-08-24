// Copyright 2026 The ChromiumOS Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

import {Component, Input, inject} from '@angular/core';
import {Router} from '@angular/router';
import {MatButtonModule} from '@angular/material/button';
import {MatIconModule} from '@angular/material/icon';

@Component({
  selector: 'app-maui-station-footer',
  standalone: true,
  templateUrl: './maui-station-footer.html',
  styleUrl: './maui-station-footer.scss',
  imports: [MatButtonModule, MatIconModule],
})
export class MauiStationFooterComponent {
  private readonly router = inject(Router);

  @Input({required: true}) nextStationRoute!: string;
  @Input({required: true}) nextStationLabel!: string;

  refresh(): void {
    window.location.reload();
  }

  goToNext(): void {
    void this.router.navigate([this.nextStationRoute]);
  }
}
