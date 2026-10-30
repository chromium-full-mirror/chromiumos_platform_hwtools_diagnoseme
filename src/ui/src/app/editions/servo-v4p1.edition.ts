// Copyright 2026 The ChromiumOS Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

import {Edition} from './edition.model';
import {SERVO_V4P1_ROUTES, SERVO_V4P1_MENU} from './edition-routes';

/** DiagnoseMe edition: Servo v4p1. */
export const EDITION: Edition = {
  id: 'servo-v4p1',
  title: 'Servo v4p1',
  landing: '/servo-v4p1-manufacturing',
  menu: SERVO_V4P1_MENU,
  routes: SERVO_V4P1_ROUTES,
};
