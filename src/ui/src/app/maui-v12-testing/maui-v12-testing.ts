// Copyright 2026 The ChromiumOS Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

import {Component} from '@angular/core';
import {CommonModule} from '@angular/common';
import {MauiFunctionalSuiteComponent} from '../maui-common/maui-functional-suite/maui-functional-suite';

@Component({
  selector: 'app-maui-v12-testing',
  standalone: true,
  templateUrl: './maui-v12-testing.html',
  styleUrl: './maui-v12-testing.scss',
  imports: [CommonModule, MauiFunctionalSuiteComponent],
})
export class MauiV12TestingComponent {}
