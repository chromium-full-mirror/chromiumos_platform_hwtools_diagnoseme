// Copyright 2025 The ChromiumOS Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

import {NgModule} from '@angular/core';
import {RouterModule, Routes} from '@angular/router';
import {DolosProvisionComponent} from './dolos-provision/dolos-provision';
import {ServoV4p1ManufacturingComponent} from './servo-v4p1-manufacturing/servo-v4p1-manufacturing';
import {ServoMicroManufacturingComponent} from './servo-micro-manufacturing/servo-micro-manufacturing';
import {MauiStep1ProvisioningComponent} from './maui-step1-provisioning/maui-step1-provisioning';
import {MauiStep2FunctionalComponent} from './maui-step2-functional/maui-step2-functional';
import {MauiStep3LabelingComponent} from './maui-step3-labeling/maui-step3-labeling';
import {MauiStep4Qt0Component} from './maui-step4-qt0/maui-step4-qt0';

const routes: Routes = [
  // 4 Independent Manufacturing Steps
  {
    path: 'maui-step1',
    component: MauiStep1ProvisioningComponent,
  },
  {
    path: 'maui-step2',
    component: MauiStep2FunctionalComponent,
  },
  {
    path: 'maui-step3',
    component: MauiStep3LabelingComponent,
  },
  {
    path: 'maui-step4',
    component: MauiStep4Qt0Component,
  },

  // Legacy route aliases for backward compatibility
  {
    path: 'maui-initial-flashing',
    redirectTo: '/maui-step1',
    pathMatch: 'full',
  },
  {
    path: 'maui-pdc-flashing',
    redirectTo: '/maui-step1',
    pathMatch: 'full',
  },
  {
    path: 'maui-host-testing',
    redirectTo: '/maui-step2',
    pathMatch: 'full',
  },
  {
    path: 'maui-dut-testing',
    redirectTo: '/maui-step2',
    pathMatch: 'full',
  },
  {
    path: 'maui-labeling',
    redirectTo: '/maui-step3',
    pathMatch: 'full',
  },

  // Other boards
  {path: 'dolos-rework', component: DolosProvisionComponent},
  {
    path: 'servo-v4p1-manufacturing',
    component: ServoV4p1ManufacturingComponent,
  },
  {
    path: 'servo-micro-manufacturing',
    component: ServoMicroManufacturingComponent,
  },
  {path: '', redirectTo: '/maui-step1', pathMatch: 'full'},
];

@NgModule({
  imports: [RouterModule.forRoot(routes)],
  exports: [RouterModule],
})
export class AppRoutingModule {}
