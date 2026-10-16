// Copyright 2026 The ChromiumOS Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

import {Edition} from './edition.model';
import {MAUI_V12_ROUTES, MAUI_V12_MENU} from './edition-routes';

/** DiagnoseMe edition: Maui v1.2 (ITE IT82002, native USB). */
export const EDITION: Edition = {
  id: 'maui-v1.2',
  title: 'Maui v1.2',
  landing: '/maui/v1.2/testing',
  menu: MAUI_V12_MENU,
  routes: MAUI_V12_ROUTES,
};
