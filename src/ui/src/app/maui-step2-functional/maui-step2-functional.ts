// Copyright 2026 The ChromiumOS Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

import {Component} from '@angular/core';
import {CommonModule} from '@angular/common';
import {MauiFunctionalSuiteComponent} from '../maui-common/maui-functional-suite/maui-functional-suite';

@Component({
  selector: 'app-maui-step2-functional',
  standalone: true,
  templateUrl: './maui-step2-functional.html',
  styleUrl: './maui-step2-functional.scss',
  imports: [CommonModule, MauiFunctionalSuiteComponent],
})
export class MauiStep2FunctionalComponent {}
