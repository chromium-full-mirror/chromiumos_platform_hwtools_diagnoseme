// Copyright 2026 The ChromiumOS Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

import {Edition} from './edition.model';
import {
  DOLOS_MENU,
  DOLOS_ROUTES,
  SERVO_MICRO_MENU,
  SERVO_MICRO_ROUTES,
  SERVO_V4P1_MENU,
  SERVO_V4P1_ROUTES,
} from './edition-routes';

/**
 * DiagnoseMe edition: Servo / Dolos.
 *
 * Every page of the Servo and Dolos stations, as before the editions (the
 * 'diagnoseme' package and the :release images).
 */
export const EDITION: Edition = {
  id: 'servo-dolos',
  title: 'Servo / Dolos',
  landing: '/dolos-rework',
  menu: [...DOLOS_MENU, ...SERVO_V4P1_MENU, ...SERVO_MICRO_MENU],
  routes: [...DOLOS_ROUTES, ...SERVO_V4P1_ROUTES, ...SERVO_MICRO_ROUTES],
};
