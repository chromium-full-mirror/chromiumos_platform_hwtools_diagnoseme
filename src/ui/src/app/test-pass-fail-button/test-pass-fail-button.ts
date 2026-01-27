// Copyright 2025 The ChromiumOS Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

import {Component} from '@angular/core';

import {MatIconModule} from '@angular/material/icon';
import {MatButtonModule} from '@angular/material/button';
import {CommonModule} from '@angular/common';

@Component({
  selector: 'test-pass-fail-button',
  standalone: true,
  templateUrl: './test-pass-fail-button.html',
  styleUrl: './test-pass-fail-button.scss',
  imports: [MatIconModule, MatButtonModule, CommonModule],
})
export class TestPassFailButton {
  statusIcon = 'check';
  statusCSS = 'test-pass-button';
  display = false;

  showPassed = () => {
    this.display = true;
    this.statusIcon = 'check';
    this.statusCSS = 'test-pass-button';
  };
  showWorking = () => {
    this.display = true;
    this.statusIcon = 'cycle';
    this.statusCSS = 'test-working-button';
  };
  showFailed = () => {
    this.display = true;
    this.statusIcon = 'close';
    this.statusCSS = 'test-fail-button';
  };
  hide = () => {
    this.display = false;
  };
}
