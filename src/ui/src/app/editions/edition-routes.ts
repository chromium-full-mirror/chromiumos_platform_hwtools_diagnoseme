// Copyright 2026 The ChromiumOS Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

import {Routes} from '@angular/router';
import {EditionMenuItem} from './edition.model';

/** Dolos rework routes. */
export const DOLOS_ROUTES: Routes = [
  {
    path: 'dolos-rework',
    loadComponent: () =>
      import('../dolos-provision/dolos-provision').then(
        m => m.DolosProvisionComponent,
      ),
  },
];

/** Servo v4p1 manufacturing routes. */
export const SERVO_V4P1_ROUTES: Routes = [
  {
    path: 'servo-v4p1-manufacturing',
    loadComponent: () =>
      import('../servo-v4p1-manufacturing/servo-v4p1-manufacturing').then(
        m => m.ServoV4p1ManufacturingComponent,
      ),
  },
];

/** Servo Micro manufacturing routes. */
export const SERVO_MICRO_ROUTES: Routes = [
  {
    path: 'servo-micro-manufacturing',
    loadComponent: () =>
      import('../servo-micro-manufacturing/servo-micro-manufacturing').then(
        m => m.ServoMicroManufacturingComponent,
      ),
  },
];

/** Maui v1.1 (TI MSPM0 + FTDI) routes, including legacy URL redirects. */
export const MAUI_V11_ROUTES: Routes = [
  {
    path: 'maui/v1.1/provisioning',
    loadComponent: () =>
      import('../maui-step1-provisioning/maui-step1-provisioning').then(
        m => m.MauiStep1ProvisioningComponent,
      ),
  },
  {
    path: 'maui/v1.1/functional',
    loadComponent: () =>
      import('../maui-step2-functional/maui-step2-functional').then(
        m => m.MauiStep2FunctionalComponent,
      ),
  },
  {
    path: 'maui/v1.1/labeling',
    loadComponent: () =>
      import('../maui-step3-labeling/maui-step3-labeling').then(
        m => m.MauiStep3LabelingComponent,
      ),
  },
  {
    path: 'maui/v1.1/qt0',
    loadComponent: () =>
      import('../maui-step4-qt0/maui-step4-qt0').then(
        m => m.MauiStep4Qt0Component,
      ),
  },
  // Legacy route aliases (installed stations and bookmarks).
  {
    path: 'maui-step1',
    redirectTo: '/maui/v1.1/provisioning',
    pathMatch: 'full',
  },
  {path: 'maui-step2', redirectTo: '/maui/v1.1/functional', pathMatch: 'full'},
  {path: 'maui-step3', redirectTo: '/maui/v1.1/labeling', pathMatch: 'full'},
  {path: 'maui-step4', redirectTo: '/maui/v1.1/qt0', pathMatch: 'full'},
  {
    path: 'maui-initial-flashing',
    redirectTo: '/maui/v1.1/provisioning',
    pathMatch: 'full',
  },
  {
    path: 'maui-pdc-flashing',
    redirectTo: '/maui/v1.1/provisioning',
    pathMatch: 'full',
  },
  {
    path: 'maui-host-testing',
    redirectTo: '/maui/v1.1/functional',
    pathMatch: 'full',
  },
  {
    path: 'maui-dut-testing',
    redirectTo: '/maui/v1.1/functional',
    pathMatch: 'full',
  },
  {
    path: 'maui-labeling',
    redirectTo: '/maui/v1.1/labeling',
    pathMatch: 'full',
  },
];

export const DOLOS_MENU: EditionMenuItem[] = [
  {label: 'Dolos Rework', route: '/dolos-rework'},
];

export const SERVO_V4P1_MENU: EditionMenuItem[] = [
  {label: 'Servo Manufacturing', route: '/servo-v4p1-manufacturing'},
];

export const SERVO_MICRO_MENU: EditionMenuItem[] = [
  {label: 'Servo Micro Manufacturing', route: '/servo-micro-manufacturing'},
];

export const MAUI_V11_MENU: EditionMenuItem[] = [
  {
    label: 'SMT Step 1: MCU & FTDI Provisioning + PDC Flashing',
    route: '/maui/v1.1/provisioning',
  },
  {
    label: 'SMT Step 2: SMT Functional Verification',
    route: '/maui/v1.1/functional',
  },
  {divider: true},
  {
    label: 'FATP Step 3: Final Provisioning & Label Printing',
    route: '/maui/v1.1/labeling',
  },
  {
    label: 'FATP Step 4: Full-Unit Quick Test (QT0)',
    route: '/maui/v1.1/qt0',
  },
];

/** Maui v1.2 (ITE IT82002, native USB CDC-ACM) routes. */
export const MAUI_V12_ROUTES: Routes = [
  {
    path: 'maui/v1.2/testing',
    loadComponent: () =>
      import('../maui-v12-testing/maui-v12-testing').then(
        m => m.MauiV12TestingComponent,
      ),
  },
  {
    path: 'maui/v1.2/programming',
    loadComponent: () =>
      import('../maui-v12-programming/maui-v12-programming').then(
        m => m.MauiV12ProgrammingComponent,
      ),
  },
  {
    path: 'maui/v1.2/serial',
    loadComponent: () =>
      import('../maui-v12-serial/maui-v12-serial').then(
        m => m.MauiV12SerialComponent,
      ),
  },
];

export const MAUI_V12_MENU: EditionMenuItem[] = [
  {
    label: 'Testing: Serial Number & Functional Tests',
    route: '/maui/v1.2/testing',
  },
  {label: 'Programming: MCU App, PDC, H2H', route: '/maui/v1.2/programming'},
  {
    label: 'Enumeration Status, Serial Number & Barcode',
    route: '/maui/v1.2/serial',
  },
];
