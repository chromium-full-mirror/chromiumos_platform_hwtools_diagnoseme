// Copyright 2026 The ChromiumOS Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

import {Edition} from './edition.model';
import {
  DOLOS_MENU,
  DOLOS_ROUTES,
  MAUI_V11_MENU,
  MAUI_V11_ROUTES,
  MAUI_V12_MENU,
  MAUI_V12_ROUTES,
  SERVO_MICRO_MENU,
  SERVO_MICRO_ROUTES,
  SERVO_V4P1_MENU,
  SERVO_V4P1_ROUTES,
} from './edition-routes';

/**
 * Development / CQ edition containing every page.
 *
 * This edition is never shipped to users (no release trigger, no ISO).
 */
export const EDITION: Edition = {
  id: 'all',
  title: 'All (development)',
  landing: '/maui/v1.1/provisioning',
  menu: [
    ...DOLOS_MENU,
    ...SERVO_V4P1_MENU,
    ...SERVO_MICRO_MENU,
    {divider: true},
    ...MAUI_V11_MENU,
    {divider: true},
    ...MAUI_V12_MENU,
  ],
  routes: [
    ...DOLOS_ROUTES,
    ...SERVO_V4P1_ROUTES,
    ...SERVO_MICRO_ROUTES,
    ...MAUI_V11_ROUTES,
    ...MAUI_V12_ROUTES,
  ],
};
