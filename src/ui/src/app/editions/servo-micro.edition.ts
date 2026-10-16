// Copyright 2026 The ChromiumOS Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

import {Edition} from './edition.model';
import {SERVO_MICRO_ROUTES, SERVO_MICRO_MENU} from './edition-routes';

/** DiagnoseMe edition: Servo Micro. */
export const EDITION: Edition = {
  id: 'servo-micro',
  title: 'Servo Micro',
  landing: '/servo-micro-manufacturing',
  menu: SERVO_MICRO_MENU,
  routes: SERVO_MICRO_ROUTES,
};
