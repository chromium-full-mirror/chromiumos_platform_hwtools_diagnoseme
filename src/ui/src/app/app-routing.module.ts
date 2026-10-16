// Copyright 2025 The ChromiumOS Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

import {NgModule} from '@angular/core';
import {RouterModule, Routes} from '@angular/router';
import {ACTIVE_EDITION} from './editions/active-edition';

// Routes are defined per edition (see ./editions). Only the routes of the
// edition selected at build time are compiled into the bundle.
const routes: Routes = [
  ...ACTIVE_EDITION.routes,
  {path: '', redirectTo: ACTIVE_EDITION.landing, pathMatch: 'full'},
  {path: '**', redirectTo: ACTIVE_EDITION.landing},
];

@NgModule({
  imports: [RouterModule.forRoot(routes)],
  exports: [RouterModule],
})
export class AppRoutingModule {}
