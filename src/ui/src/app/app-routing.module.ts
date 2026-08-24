// Copyright 2025 The ChromiumOS Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

import {NgModule} from '@angular/core';
import {RouterModule, Routes} from '@angular/router';
import {DolosProvisionComponent} from './dolos-provision/dolos-provision';
import {ServoV4p1ManufacturingComponent} from './servo-v4p1-manufacturing/servo-v4p1-manufacturing';
import {ServoMicroManufacturingComponent} from './servo-micro-manufacturing/servo-micro-manufacturing';
import {MauiInitialFlashingComponent} from './maui-initial-flashing/maui-initial-flashing';
import {MauiPdcFlashingComponent} from './maui-pdc-flashing/maui-pdc-flashing';
import {MauiHostTestingComponent} from './maui-host-testing/maui-host-testing';
import {MauiDutTestingComponent} from './maui-dut-testing/maui-dut-testing';
import {MauiLabelingComponent} from './maui-labeling/maui-labeling';

const routes: Routes = [
  {path: 'dolos-rework', component: DolosProvisionComponent},
  {
    path: 'servo-v4p1-manufacturing',
    component: ServoV4p1ManufacturingComponent,
  },
  {
    path: 'servo-micro-manufacturing',
    component: ServoMicroManufacturingComponent,
  },
  {
    path: 'maui-initial-flashing',
    component: MauiInitialFlashingComponent,
  },
  {
    path: 'maui-pdc-flashing',
    component: MauiPdcFlashingComponent,
  },
  {
    path: 'maui-host-testing',
    component: MauiHostTestingComponent,
  },
  {
    path: 'maui-dut-testing',
    component: MauiDutTestingComponent,
  },
  {
    path: 'maui-labeling',
    component: MauiLabelingComponent,
  },
  {path: '', redirectTo: '/dolos-rework', pathMatch: 'full'},
];

@NgModule({
  imports: [RouterModule.forRoot(routes)],
  exports: [RouterModule],
})
export class AppRoutingModule {}
